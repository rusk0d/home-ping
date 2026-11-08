package com.david.homeenv

import android.os.Bundle
import android.view.MotionEvent
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.david.homeenv.databinding.ActivityDevicesBinding
import com.david.homeenv.ui.AccentCard
import com.david.homeenv.ui.Levels
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/**
 * Devices (#322): one card per node with what it reports about itself: WiFi signal, uptime,
 * heap, the last reset reason and restarts. `/api/devices` (`health`, the values Grafana gets
 * from `/api/metrics`) and `/api/restarts`; below them the server's checks (`/api/server_status`,
 * moved from Home, #325) and above them what needs a look (DeviceIssues, the red dot on Home's
 * devices button). Opened from that button, a swipe right on Home (a swipe left goes back) and
 * node alerts in the notification history (#321).
 */
class DevicesActivity : AppCompatActivity() {

    private lateinit var b: ActivityDevicesBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityDevicesBinding.inflate(layoutInflater)
        setContentView(b.root)
        SwipeNav.slideFromLeft(this)
        b.back.setOnClickListener { finish() }
        b.swipeRefresh.setOnRefreshListener { load() }
    }

    private val swipe by lazy { SwipeNav(this, onLeft = { finish() }) }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        swipe.onTouch(ev)
        return super.dispatchTouchEvent(ev)
    }

    override fun finish() {
        super.finish()
        SwipeNav.slideBackLeft(this)
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            val (devices, restarts, server) = withContext(Dispatchers.IO) {
                Triple(Api.getArray("/api/devices"), Api.getObject("/api/restarts"), Api.getObject("/api/server_status"))
            }
            b.swipeRefresh.isRefreshing = false
            render(devices, restarts, server)
        }
    }

    private fun render(devices: JSONArray?, restarts: JSONObject?, server: JSONObject?) {
        b.cards.removeAllViews()
        if (devices == null) {
            b.status.text = "Can't reach the server"
            return
        }
        b.status.text = if (devices.length() == 0) "No nodes have reported yet"
            else "Values the nodes sent in the last few minutes; pull down to refresh"
        val issues = DeviceIssues.list(devices, restarts, server)
        if (issues.isNotEmpty()) b.cards.addView(issuesCard(issues))
        for (i in 0 until devices.length()) {
            val d = devices.optJSONObject(i) ?: continue
            b.cards.addView(card(d, restarts?.optJSONObject(d.optString("device"))))
        }
        server?.let {
            b.cards.addView(heading("Server"))
            b.cards.addView(newCard().also { card -> ServerStatus.fill(card, it) })
        }
    }

    /** What needs a look, in red: the same list as the dot on Home's devices button. */
    private fun issuesCard(issues: List<String>): AccentCard = newCard().apply {
        accentColor = Levels.color(context, R.color.bad)
        addView(TextView(context).apply {
            text = if (issues.size == 1) "1 thing needs a look" else "${issues.size} things need a look"
            setTextColor(Levels.color(context, R.color.bad))
            textSize = 15f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        issues.forEach { addView(row("", it, R.color.bad)) }
    }

    private fun heading(text: String): TextView {
        val dens = resources.displayMetrics.density
        return TextView(this).apply {
            this.text = text
            setTextColor(Levels.color(context, R.color.muted))
            textSize = 13f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            isAllCaps = true
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = (12 * dens).toInt(); bottomMargin = (8 * dens).toInt() }
        }
    }

    /** An empty card with the screen's padding and spacing. */
    private fun newCard(): AccentCard {
        val dens = resources.displayMetrics.density
        return AccentCard(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding((16 * dens).toInt(), (14 * dens).toInt(), (16 * dens).toInt(), (12 * dens).toInt())
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { bottomMargin = (12 * dens).toInt() }
        }
    }

    private fun card(d: JSONObject, restarts: JSONObject?): AccentCard {
        val id = d.optString("device")
        val name = d.optString("name").ifEmpty { id }
        val online = d.optBoolean("online")
        val known = d.optBoolean("known", true)   // in the backend's registry (#359)
        val h = d.optJSONObject("health") ?: JSONObject()

        val lastSeen = d.optTs("last_seen")
        val meta = listOfNotNull(
            if (online) "online" else "offline",
            lastSeen?.takeIf { !online }?.let { "last seen ${HomeFormat.whenText(it)}" },
            id.takeIf { it != name },
            "not in the device list".takeIf { !known },
        ).joinToString(" · ")

        return newCard().apply {
            // A node not in the registry (e.g. retired) being offline is no issue: grey, not red
            accentColor = Levels.color(context, when {
                online -> R.color.good
                known -> R.color.bad
                else -> R.color.faint
            })
            addView(HomeFormat.statusLine(context, name, meta, if (online || known) online else null))

            val rows = mutableListOf<Triple<String, String, Int?>>()
            h.optNum("rssi")?.toInt()?.let { rssi ->
                val (quality, color) = signal(rssi)
                rows += Triple("WiFi", "$rssi dBm · $quality", color)
            }
            h.optNum("uptime")?.toInt()?.let { rows += Triple("Uptime", HomeFormat.uptime(it), null) }
            h.optNum("heap_free")?.let { free ->
                val parts = listOfNotNull(
                    "${kb(free)} free",
                    h.optNum("heap_min")?.let { "lowest ${kb(it)}" },
                    h.optNum("heap_block")?.let { "largest block ${kb(it)}" },
                )
                rows += Triple("Heap", parts.joinToString(" · "), null)
            }
            if (h.has("radar_connected")) {
                val ok = h.optBoolean("radar_connected")
                rows += Triple("Radar", if (ok) "sending frames" else "not responding", if (ok) null else R.color.bad)
            }
            h.optString("reset_reason").takeIf { it.isNotEmpty() }?.let { reason ->
                rows += Triple("Last reset", reason, if (HomeFormat.BAD_RESET_REASONS.any { reason.contains(it) }) R.color.bad else null)
            }
            restarts?.let { r ->
                val day = r.optInt("last_24h")
                val week = r.optInt("last_7d")
                val last = r.optTs("last_restart")?.let { " · last ${HomeFormat.whenText(it)}" }.orEmpty()
                rows += Triple("Restarts", "$day in 24 h · $week in 7 d$last", if (day > DeviceIssues.MAX_RESTARTS_24H) R.color.bad else null)
            }
            if (rows.isEmpty()) rows += Triple("", "No recent health data from this node", null)
            rows.forEach { (label, value, color) -> addView(row(label, value, color)) }
        }
    }

    /** "Label   value" (just the value when [label] is empty), the value in [color] when it's worth a look. */
    private fun row(label: String, value: String, color: Int?): LinearLayout {
        val dens = resources.displayMetrics.density
        return LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = (8 * dens).toInt() }
            if (label.isNotEmpty()) addView(TextView(context).apply {
                text = label
                setTextColor(Levels.color(context, R.color.muted))
                textSize = 13f
                layoutParams = LinearLayout.LayoutParams((92 * dens).toInt(), ViewGroup.LayoutParams.WRAP_CONTENT)
            })
            addView(TextView(context).apply {
                text = value
                setTextColor(Levels.color(context, color ?: R.color.text))
                textSize = 13f
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            })
        }
    }

    private fun kb(bytes: Double) = "%.0f KB".format(Locale.US, bytes / 1024)

    /** The usual WiFi bands: from about -70 dBm on, drops and slow reconnects get likely. */
    private fun signal(rssi: Int): Pair<String, Int?> = when {
        rssi >= -60 -> "good" to null
        rssi >= -70 -> "fair" to null
        rssi >= -80 -> "weak" to R.color.ok
        else -> "poor" to R.color.bad
    }
}
