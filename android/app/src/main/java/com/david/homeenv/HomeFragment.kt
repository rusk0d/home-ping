package com.david.homeenv

import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.david.homeenv.databinding.FragmentHomeBinding
import com.david.homeenv.ui.Levels
import org.json.JSONArray
import org.json.JSONObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// Home screen, styled after the web dashboard (#30). Endpoints: contracts/api.md
class HomeFragment : Fragment() {

    companion object {
        private const val TAG = "HomeFragment"
        private const val REFRESH_MS = 60_000L
        private const val AGE_TICK_MS = 10_000L
    }

    private val handler = Handler(Looper.getMainLooper())

    /** The card renderers (HomeCards.kt, #306); they hold the binding, so they live with the view. */
    private class Cards(b: FragmentHomeBinding) {
        val room = RoomCard(b)
        val co2 = Co2Card(b)
        val occupancyNow = OccupancyNowCard(b)
        val fridge = FridgeCard(b)
        val pressure = PressureCard(b)
        val hourly = HourlyCard(b)
        val devices = DevicesCard(b)
        val server = ServerCard(b)
        val restarts = RestartsCard(b)
        val stats = StatsCard(b)
        /** The ones with a reading time ("x ago", stale dimming) */
        val aged = listOf(room, co2, fridge)
    }
    private var cards: Cards? = null

    // Offline banner: set when /api/latest_all can't be fetched; lastOkAt = last successful load
    private var serverDown = false
    private var lastOkAt: Long? = null

    private val refreshLoop = object : Runnable {
        override fun run() {
            loadDashboardData()
            handler.postDelayed(this, REFRESH_MS)
        }
    }
    private val ageLoop = object : Runnable {
        override fun run() {
            updateAges()
            handler.postDelayed(this, AGE_TICK_MS)
        }
    }

    // View binding; only valid between onCreateView and onDestroyView
    private var _b: FragmentHomeBinding? = null
    private val b get() = _b!!

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _b = FragmentHomeBinding.inflate(inflater, container, false)
        cards = Cards(b)
        return b.root
    }

    override fun onDestroyView() {
        super.onDestroyView()
        cards = null
        _b = null
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        b.occLegend.text = buildLegend()
        val bad = Levels.color(requireContext(), R.color.bad)
        b.offlineBanner.setTextColor(bad)
        b.offlineBanner.background = GradientDrawable().apply {
            cornerRadius = 12 * resources.displayMetrics.density
            setColor(Levels.withAlpha(bad, 0.14f))
        }
        b.swipeRefresh.setOnRefreshListener { loadDashboardData() }
        b.historyButton.setOnClickListener { startActivity(Intent(requireContext(), HistoryActivity::class.java)) }
    }

    override fun onResume() {
        super.onResume()
        // Re-add the location auto-arm geofence each time (it replaces itself; the controls are
        // in the notification settings since #236): Play services can drop it, e.g. after its
        // data is cleared or location was off, and nothing else would notice (#239)
        HomeGeofence.register(requireContext())
        handler.post(refreshLoop)
        handler.postDelayed(ageLoop, AGE_TICK_MS)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(refreshLoop)
        handler.removeCallbacks(ageLoop)
    }

    // ---------- networking (Api: blocking, null on failure) ----------
    // Coroutines in the view's lifecycle scope: the blocking calls run on Dispatchers.IO,
    // and the results are dropped if the view is gone by then (#125).

    private class Snapshot(
        val latest: JSONObject?,
        val devices: JSONArray?,
        val stats: JSONObject?,
        val restarts: JSONObject?,
        val air: JSONObject?,
        val hourly: JSONArray?,
        val server: JSONObject?,
    )

    /**
     * /api/dashboard (#128), then /api/server_status (#260) only if that worked; all null when
     * it fails. Blocking: call it on Dispatchers.IO.
     */
    private fun loadSnapshot(): Snapshot {
        val d = Api.getObject("/api/dashboard")
        return Snapshot(
            latest = d?.optJSONObject("latest_all"),
            devices = d?.optJSONArray("devices"),
            stats = d?.optJSONObject("stats24h_combined"),
            restarts = d?.optJSONObject("restarts"),
            air = d?.optJSONObject("air"),
            hourly = d?.optJSONArray("hourly"),
            server = d?.let { Api.getObject("/api/server_status") },
        )
    }

    private fun loadDashboardData() {
        viewLifecycleOwner.lifecycleScope.launch {
            val snap = withContext(Dispatchers.IO) { loadSnapshot() }
            serverDown = snap.latest == null
            if (!serverDown) lastOkAt = System.currentTimeMillis()
            try {
                render(snap)
            } catch (e: Exception) {
                // Never crash the screen on an unexpected payload
                Log.w(TAG, "render failed", e)
            }
            b.swipeRefresh.isRefreshing = false
        }
    }

    // ---------- rendering ----------

    private fun render(s: Snapshot) {
        val c = cards ?: return
        val devices = Device.list(s.devices)
        s.latest?.let {
            c.room.render(it, s.air)
            c.co2.render(it, s.air)
            c.occupancyNow.render(it)
            c.fridge.render(it, devices)
        }
        s.air?.let { c.pressure.render(it) }
        s.hourly?.let { c.hourly.render(it) }
        if (s.devices != null) c.devices.render(devices, s.latest)
        s.restarts?.let { c.restarts.render(it, devices) }
        s.stats?.let { c.stats.render(it) }
        s.server?.let { c.server.render(it) }
        updateAges()
    }

    private fun updateAges() {
        if (view == null) return
        val now = System.currentTimeMillis()
        renderBanner(now)
        cards?.aged?.forEach { it.updateAge(now) }
    }

    private fun renderBanner(now: Long) {
        b.offlineBanner.visibility = if (serverDown) View.VISIBLE else View.GONE
        if (!serverDown) return
        val since = lastOkAt?.let { " · last data ${HomeFormat.age((now - it) / 1000)}" }.orEmpty()
        b.offlineBanner.text = "Can't reach the server$since"
    }

    private fun buildLegend(): CharSequence {
        val ctx = requireContext()
        val items = listOf(R.color.good to "≥60%", R.color.ok to "20–59%", R.color.muted to "1–19%", R.color.faint to "0%")
        return SpannableStringBuilder().apply {
            items.forEachIndexed { i, (color, label) ->
                if (i > 0) append("   ")
                val start = length
                append("■")
                setSpan(ForegroundColorSpan(Levels.color(ctx, color)), start, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                append(" ").append(label)
            }
            append("   hatched: no data")
        }
    }
}
