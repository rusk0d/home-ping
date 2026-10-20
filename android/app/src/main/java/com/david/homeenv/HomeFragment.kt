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
import android.text.style.RelativeSizeSpan
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.david.homeenv.databinding.FragmentHomeBinding
import com.david.homeenv.ui.AccentCard
import com.david.homeenv.ui.Level
import com.david.homeenv.ui.Levels
import com.david.homeenv.ui.SparklineView
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.time.Instant
import java.util.Calendar
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// Home screen, styled after the web dashboard (#30). Endpoints: contracts/api.md
class HomeFragment : Fragment() {

    companion object {
        private const val TAG = "HomeFragment"
        private const val REFRESH_MS = 60_000L
        private const val AGE_TICK_MS = 10_000L
        // Seconds without a new reading before a card is dimmed (~3 missed publishes, as on the web)
        private const val STALE_ENV_S = 90
        private const val STALE_CO2_S = 180

        private const val MAIN = "home_ping_esp32"
        private const val HALLWAY = "hallway_presence_esp32"
        private val DEVICE_NAMES = linkedMapOf(MAIN to "Main node", HALLWAY to "Hallway radar")
        // Reset reasons (backend store.RESET_REASONS) that mean a crash or a power problem
        private val BAD_RESET_REASONS = listOf("panic", "watchdog", "brownout", "glitch", "lockup")
    }

    private val handler = Handler(Looper.getMainLooper())

    // Timestamps of the latest readings, for the "x ago" labels and stale dimming
    private var envTs: Date? = null
    private var co2Ts: Date? = null

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
        return b.root
    }

    override fun onDestroyView() {
        super.onDestroyView()
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
        s.latest?.let { renderLatest(it, s.air) }
        s.air?.let { renderPressure(it) }
        s.hourly?.let { renderHourly(it) }
        renderDevices(s.devices, s.latest)
        s.restarts?.let { renderRestarts(it) }
        s.stats?.let { renderStats(it) }
        s.server?.let { renderServer(it) }
        updateAges()
    }

    private fun renderLatest(latest: JSONObject, air: JSONObject?) {
        // Temperature + humidity from the device with the newest env reading (the BME280
        // moved boards in the node merge, #57)
        val main = latest.newestBy("env_ts")
        val temp = main?.optNum("temp")
        val hum = main?.optNum("humidity")
        b.tempValue.text = valueWithUnit(temp?.let { "%.1f".format(Locale.US, it) }, "°C")
        b.humidityValue.text = valueWithUnit(hum?.let { "%.0f".format(Locale.US, it) }, "%")
        Levels.applyChip(b.tempChip, temp?.let(Levels::temperature))
        // Humid for hours (/api/air mould_risk, #177): the chip says so instead of the level
        val mould = air?.optBoolean("mould_risk") == true
        Levels.applyChip(b.humidityChip, if (mould && hum != null) Levels.MOULD_RISK else hum?.let(Levels::humidity))
        val dew = if (temp != null && hum != null) Levels.dewPoint(temp, hum) else null
        b.humidityDew.text = dew?.let { "dew %.0f°C".format(Locale.US, it) } ?: ""
        // env_ts is the env reading's own time; `ts` can be the radar's (contracts/api.md, #11)
        envTs = main?.optTs("env_ts")

        // CO2 from the device with the newest CO2 reading (the main node since the merge,
        // #57). `co2` is null whenever the reading isn't valid.
        val co2Node = latest.newestBy("co2_ts")
        val ppm = co2Node?.optNum("co2")?.toInt()
        val sensorOk = co2Node?.takeIf { it.has("co2_ok") && !it.isNull("co2_ok") }?.optBoolean("co2_ok")
        b.co2Value.text = ppm?.toString() ?: "--"
        b.co2Gauge.ppm = ppm
        if (ppm != null) {
            // The server's band (/api/air), when it is about this same reading (#127)
            val serverBand = air?.takeIf { it.optNum("co2")?.toInt() == ppm }?.optString("band")
            val level = Levels.co2Band(serverBand) ?: Levels.co2(ppm)
            Levels.applyChip(b.co2Chip, level)
            b.cardCo2.accentColor = Levels.color(requireContext(), level.color)
        } else {
            Levels.applyChip(b.co2Chip, if (co2Node != null) Level("No valid reading", R.color.ok) else null)
            b.cardCo2.accentColor = Levels.color(requireContext(), R.color.faint)
        }
        b.co2SensorStatus.text = when (sensorOk) {
            true -> "sensor OK"
            false -> "sensor fault / warming up"
            null -> "sensor --"
        }
        co2Ts = co2Node?.optTs("co2_ts")

        // Occupied now: combined radar state
        latest.optJSONObject("combined")?.let { combined ->
            // Both null when no radar reported recently
            val occupied = combined.optNum("occupied")?.let { it == 1.0 }
            b.occupiedValue.text = when (occupied) { true -> "Yes"; false -> "No"; null -> "--" }
            Levels.applyChip(b.occupiedChip, when (occupied) {
                true -> Level("Occupied", R.color.good)
                false -> Level("Vacant", R.color.muted)
                null -> null
            })
            b.cardOccNow.accentColor = Levels.color(requireContext(), if (occupied == true) R.color.good else R.color.faint)
            b.confidenceValue.text = "confidence " + (combined.optNum("confidence")?.let { "%.0f%%".format(Locale.US, it) } ?: "--")
        }
    }

    private fun renderPressure(air: JSONObject) {
        val pressure = air.optNum("pressure")
        b.pressureValue.text = valueWithUnit(pressure?.let { "%.0f".format(Locale.US, it) }, "hPa")
        val delta = air.optNum("pressure_3h_change")
        Levels.applyChip(b.pressureChip, delta?.let(Levels::pressureTrend))
        b.pressureTrend.text = delta?.let { "3 h: %+.1f hPa".format(Locale.US, it) } ?: "3 h: not enough data"
    }

    private fun renderHourly(hourly: JSONArray) {
        // Buckets since the server's local midnight, newest first; a missing hour = no
        // data. "Today" and the current hour come from the newest bucket (as on the web)
        // so the chart is right even if the phone is in another timezone than the server.
        val newest = hourly.optJSONObject(0)?.optString("hour").orEmpty()
        val today = if (newest.length >= 13) newest.substring(0, 10)
            else SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
        val current = newest.takeIf { it.length >= 13 }?.substring(11, 13)?.toIntOrNull()
            ?: Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
        val byHour = mutableMapOf<Int, Double>()
        val temps = sortedMapOf<Int, Double>()
        val hums = sortedMapOf<Int, Double>()
        for (i in 0 until hourly.length()) {
            val item = hourly.optJSONObject(i) ?: continue
            val hour = item.optString("hour")              // "YYYY-MM-DD HH:00"
            if (!hour.startsWith(today) || hour.length < 13) continue
            val h = hour.substring(11, 13).toIntOrNull() ?: continue
            item.optNum("avg_temp")?.let { temps[h] = it }
            item.optNum("avg_humidity")?.let { hums[h] = it }
            byHour[h] = item.optNum("avg_occupancy") ?: continue
        }
        // Sparklines: hourly averages since midnight (/api/hourly; no finer data in the API yet)
        renderSpark(b.tempSpark, b.tempRange, temps.values.toList(), "%.1f", "°")
        renderSpark(b.humiditySpark, b.humidityRange, hums.values.toList(), "%.0f", "%")
        b.occupancyBars.setData(byHour, current)

        val total = byHour.values.sum() / 100.0
        val peak = byHour.filterValues { it > 0 }.maxByOrNull { it.value }
        b.occSummary.text = buildString {
            append("≈ %.1f h occupied".format(Locale.US, total))
            if (peak != null) append(" · busiest %02d:00".format(peak.key))
        }
    }

    private fun renderSpark(spark: SparklineView, range: TextView, values: List<Double>, fmt: String, unit: String) {
        spark.setPoints(values)
        range.text = if (values.size < 2) "" else
            "today ${fmt.format(Locale.US, values.min())}–${fmt.format(Locale.US, values.max())}$unit"
    }

    private fun renderDevices(devices: JSONArray?, latest: JSONObject?) {
        val online = mutableMapOf<String, Boolean>()
        devices?.let {
            for (i in 0 until it.length()) {
                val d = it.optJSONObject(i) ?: continue
                online[d.optString("device")] = d.optBoolean("online")
            }
        }
        b.devicesList.removeAllViews()
        DEVICE_NAMES.forEach { (id, name) ->
            val uptime = latest?.optJSONObject(id)?.optNum("uptime")?.toInt()
            val meta = when {
                id !in online && uptime == null -> "no data yet"
                else -> listOfNotNull(
                    if (online[id] == true) "online" else "offline",
                    uptime?.let { "up ${formatUptime(it)}" },
                ).joinToString(" · ")
            }
            b.devicesList.addView(deviceRow(name, meta, online[id]))
        }
    }

    private fun deviceRow(name: String, meta: String, online: Boolean?): View {
        val d = resources.displayMetrics.density
        return AccentCard(requireContext()).apply {
            setPadding((16 * d).toInt(), (12 * d).toInt(), (16 * d).toInt(), (12 * d).toInt())
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { bottomMargin = (8 * d).toInt() }
            addView(statusLine(name, meta, online))
        }
    }

    /** A dot (good / bad, faint for null) and a bold name over a muted line; devices and Server card. */
    private fun statusLine(name: String, meta: String, ok: Boolean?): LinearLayout {
        val ctx = requireContext()
        val d = resources.displayMetrics.density
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val dot = View(ctx).apply {
            setBackgroundResource(R.drawable.dot)
            backgroundTintList = android.content.res.ColorStateList.valueOf(
                Levels.color(ctx, when (ok) { true -> R.color.good; false -> R.color.bad; null -> R.color.faint })
            )
            layoutParams = LinearLayout.LayoutParams((8 * d).toInt(), (8 * d).toInt()).apply { marginEnd = (12 * d).toInt() }
        }
        val texts = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        texts.addView(TextView(ctx).apply {
            text = name
            setTextColor(Levels.color(ctx, R.color.text))
            textSize = 15f
            paint.isFakeBoldText = true
        })
        texts.addView(TextView(ctx).apply {
            text = meta
            setTextColor(Levels.color(ctx, R.color.muted))
            textSize = 12f
        })
        row.addView(dot)
        row.addView(texts)
        return row
    }

    /** Server card (#260): one line per check of /api/server_status, red where it fails. */
    private fun renderServer(json: JSONObject) {
        val d = resources.displayMetrics.density
        val lines = mutableListOf<Triple<String, String, Boolean?>>()

        json.optJSONObject("backend")?.let { be ->
            val mqtt = be.optBoolean("mqtt_connected")
            val up = be.optTs("started")?.let { "up ${formatUptime(((System.currentTimeMillis() - it.time) / 1000).toInt())}" }
            lines += Triple("Backend", listOfNotNull(up, if (mqtt) "MQTT connected" else "MQTT disconnected").joinToString(" · "), mqtt)
        }
        json.optJSONObject("backup")?.let { bk ->
            val last = bk.optTs("last")
            val meta = if (last == null) "none yet" else listOfNotNull(
                formatWhen(last),
                bk.optNum("size_bytes")?.let { "%.1f MB".format(Locale.US, it / 1_000_000) },
                "${bk.optInt("count")} kept",
            ).joinToString(" · ")
            lines += Triple("Backup", meta, bk.optBoolean("ok"))
        }
        json.optJSONObject("offsite")?.let { off ->
            val meta = off.optTs("last")?.let { "uploaded ${formatWhen(it)}" } ?: "never uploaded"
            lines += Triple("S3 copy", meta, off.optBoolean("ok"))
        }
        json.optJSONObject("disk")?.let { disk ->
            val mb = disk.optInt("free_mb")
            val free = if (mb >= 1024) "%.1f GB free".format(Locale.US, mb / 1024.0) else "$mb MB free"
            lines += Triple("Disk", free, disk.optBoolean("ok"))
        }
        json.optJSONArray("certs")?.let { certs ->
            for (i in 0 until certs.length()) {
                val c = certs.optJSONObject(i) ?: continue
                val expires = c.optTs("expires")
                val error = if (c.isNull("error")) null else c.optString("error")
                val detail = when {
                    error != null -> error
                    expires != null -> {
                        val days = (expires.time - System.currentTimeMillis()) / 86_400_000
                        "until ${SimpleDateFormat("yyyy-MM-dd", Locale.US).format(expires)} ($days d)"
                    }
                    else -> "not checked yet"
                }
                val ok = if (c.isNull("ok")) null else c.optBoolean("ok")
                lines += Triple("Certificate", "${c.optString("target")} · $detail", ok)
            }
        }

        b.serverCard.removeAllViews()
        lines.forEachIndexed { i, (name, meta, ok) ->
            val line = statusLine(name, meta, ok)
            if (i > 0) {
                line.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                    .apply { topMargin = (10 * d).toInt() }
            }
            b.serverCard.addView(line)
        }
        // Red strip when anything fails, as the watchdog would push
        val anyBad = lines.any { it.third == false }
        b.serverCard.accentColor = Levels.color(requireContext(), if (anyBad) R.color.bad else R.color.good)
        b.serverSection.visibility = View.VISIBLE
        b.serverCard.visibility = View.VISIBLE
    }

    private fun renderRestarts(json: JSONObject) {
        val devices = json.keys().asSequence().mapNotNull { id -> json.optJSONObject(id)?.let { id to it } }.toList()
        val total24h = devices.sumOf { it.second.optInt("last_24h") }
        // Why the newest of those restarts happened (#235, reason from #228): ISO timestamps sort as text
        val newest = devices.filter { it.second.optInt("last_24h") > 0 }.maxByOrNull { it.second.optString("last_restart") }
        val reason = newest?.second?.takeUnless { it.isNull("last_reason") }?.optString("last_reason")
        val why = newest?.first?.let { id ->
            val name = DEVICE_NAMES[id] ?: id
            if (reason != null) " (last: $name, $reason)" else " (last: $name)"
        } ?: ""
        b.restartsValue.text = when (total24h) {
            0 -> "Restarts: none in the last 24 h"
            1 -> "Restarts: 1 in the last 24 h$why"
            else -> "Restarts: $total24h in the last 24 h$why"
        }
        b.restartsValue.setTextColor(Levels.color(requireContext(), when {
            total24h == 0 -> R.color.muted
            // A crash or power problem is worth a look even once; an update or power-on isn't
            reason != null && BAD_RESET_REASONS.any { reason.contains(it) } -> R.color.bad
            total24h <= 2 -> R.color.ok
            else -> R.color.bad
        }))
    }

    private fun renderStats(stats: JSONObject) {
        b.statsAvgTemp.text = valueWithUnit(stats.optNum("avg_temp")?.let { "%.1f".format(Locale.US, it) }, "°C")
        b.statsAvgHumidity.text = valueWithUnit(stats.optNum("avg_humidity")?.let { "%.0f".format(Locale.US, it) }, "%")
        b.statsAvgOccupancy.text = valueWithUnit(stats.optNum("avg_occupancy")?.let { "%.0f".format(Locale.US, it) }, "%")
        b.statsOccupiedHours.text = stats.optNum("occupied_hours")?.let { formatHours(it) } ?: "--"
    }

    // ---------- ages / staleness ----------

    private fun updateAges() {
        if (view == null) return
        val now = System.currentTimeMillis()
        renderBanner(now)
        val envAge = envTs?.let { (now - it.time) / 1000 }
        val co2AgeS = co2Ts?.let { (now - it.time) / 1000 }
        b.tempAge.text = formatAge(envAge)
        b.humidityAge.text = formatAge(envAge)
        b.co2Age.text = formatAge(co2AgeS)
        val envStale = envAge != null && envAge > STALE_ENV_S
        b.cardTemp.stale = envStale
        b.cardHum.stale = envStale
        b.cardCo2.stale = co2AgeS != null && co2AgeS > STALE_CO2_S
    }

    private fun renderBanner(now: Long) {
        b.offlineBanner.visibility = if (serverDown) View.VISIBLE else View.GONE
        if (!serverDown) return
        val since = lastOkAt?.let { " · last data ${formatAge((now - it) / 1000)}" }.orEmpty()
        b.offlineBanner.text = "Can't reach the server$since"
    }

    // ---------- helpers ----------

    /** The device entry of /api/latest_all with the newest [tsKey] (env_ts / co2_ts), or null. */
    private fun JSONObject.newestBy(tsKey: String): JSONObject? =
        keys().asSequence().mapNotNull { optJSONObject(it) }
            .filter { it.has(tsKey) && !it.isNull(tsKey) }
            .maxByOrNull { it.optString(tsKey) }   // ISO 8601 UTC strings sort by time

    private fun JSONObject.optTs(key: String): Date? = if (isNull(key)) null else parseTs(optString(key))

    /** API timestamps are ISO 8601 UTC ("2026-10-15T18:42:15Z", #65). */
    private fun parseTs(s: String?): Date? {
        if (s.isNullOrEmpty()) return null
        return runCatching { Date.from(Instant.parse(s)) }.getOrNull()
    }

    /** "22.4" + "°C" → big number with a small muted unit. */
    private fun valueWithUnit(value: String?, unit: String): CharSequence {
        if (value == null) return "--"
        val muted = Levels.color(requireContext(), R.color.muted)
        return SpannableStringBuilder(value).apply {
            val start = length
            append(" ").append(unit)
            setSpan(RelativeSizeSpan(0.45f), start, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            setSpan(ForegroundColorSpan(muted), start, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
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

    private fun formatAge(seconds: Long?): String = when {
        seconds == null -> "waiting…"
        seconds < 5 -> "just now"
        seconds < 60 -> "${seconds}s ago"
        seconds < 3600 -> "${seconds / 60}m ago"
        else -> "${seconds / 3600}h ago"
    }

    /** "today 03:30", "yesterday 03:30" or "Oct 18 03:30", local time. */
    private fun formatWhen(t: Date): String {
        val then = Calendar.getInstance().apply { time = t }
        val now = Calendar.getInstance()
        val daysAgo = (now.get(Calendar.YEAR) - then.get(Calendar.YEAR)) * 365 +
            now.get(Calendar.DAY_OF_YEAR) - then.get(Calendar.DAY_OF_YEAR)
        val hhmm = SimpleDateFormat("HH:mm", Locale.getDefault()).format(t)
        return when (daysAgo) {
            0 -> "today $hhmm"
            1 -> "yesterday $hhmm"
            else -> SimpleDateFormat("MMM d", Locale.US).format(t) + " $hhmm"
        }
    }

    private fun formatUptime(seconds: Int): String {
        val days = seconds / 86400
        val hours = (seconds % 86400) / 3600
        val minutes = (seconds % 3600) / 60
        return when {
            days > 0 -> "${days}d ${hours}h"
            hours > 0 -> "${hours}h ${minutes}m"
            else -> "${minutes}m"
        }
    }

    private fun formatHours(hours: Double): String {
        val h = hours.toInt()
        val m = ((hours - h) * 60).toInt()
        return "${h}h ${m}m"
    }
}
