package com.david.homeenv

import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
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
import com.david.homeenv.databinding.FragmentHomeBinding
import com.david.homeenv.ui.AccentCard
import com.david.homeenv.ui.Level
import com.david.homeenv.ui.Levels
import com.david.homeenv.ui.SparklineView
import com.google.android.material.snackbar.Snackbar
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.time.Instant
import java.util.Calendar
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

// Home screen, styled after the web dashboard (#30). Endpoints: contracts/api.md
class HomeFragment : Fragment() {

    companion object {
        private const val REFRESH_MS = 60_000L
        private const val AGE_TICK_MS = 10_000L
        // Seconds without a new reading before a card is dimmed (~3 missed publishes, as on the web)
        private const val STALE_ENV_S = 90
        private const val STALE_CO2_S = 180

        private const val MAIN = "home_ping_esp32"
        private const val HALLWAY = "hallway_presence_esp32"
        // Retired in the node merge (#57): its CO2 sensor is on the main node now. Still
        // shown while the backend lists it, and used as the CO2 source by older backends.
        private const val CO2 = "co2_sensor_esp32"
        private val DEVICE_NAMES = linkedMapOf(MAIN to "Main node", HALLWAY to "Hallway radar", CO2 to "CO₂ node")
    }

    private val handler = Handler(Looper.getMainLooper())
    // Older backends (before #65) sent server-local time without a zone
    private val legacyServerTime = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)

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
        b.occupancySwitch.setOnCheckedChangeListener { _, isChecked -> setOccupancyArmed(isChecked) }
    }

    override fun onResume() {
        super.onResume()
        // Also on resume, so a change made elsewhere (another phone, the API) shows up
        loadOccupancyArmedState()
        handler.post(refreshLoop)
        handler.postDelayed(ageLoop, AGE_TICK_MS)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(refreshLoop)
        handler.removeCallbacks(ageLoop)
    }

    // ---------- networking (Api: blocking, null on failure) ----------

    /** Runs [block] on the UI thread only while the view still exists. */
    private fun ui(block: () -> Unit) {
        activity?.runOnUiThread { if (view != null) block() }
    }

    private class Snapshot(
        val latest: JSONObject?,
        val devices: JSONArray?,
        val stats: JSONObject?,
        val restarts: JSONObject?,
        val air: JSONObject?,
        val hourly: JSONArray?,
    )

    private fun loadDashboardData() {
        thread {
            val snap = Snapshot(
                latest = Api.getObject("/api/latest_all"),
                devices = Api.getArray("/api/devices"),
                stats = Api.getObject("/api/stats24h_combined"),
                restarts = Api.getObject("/api/restarts"),
                air = Api.getObject("/api/air"),
                hourly = Api.getArray("/api/hourly"),
            )
            ui {
                serverDown = snap.latest == null
                if (!serverDown) lastOkAt = System.currentTimeMillis()
                try {
                    render(snap)
                } catch (e: Exception) {
                    // Never crash the screen on an unexpected payload
                    e.printStackTrace()
                }
                b.swipeRefresh.isRefreshing = false
            }
        }
    }

    private fun setOccupancyArmed(armed: Boolean) {
        thread {
            val ok = Api.post("/api/occupancy_arm", JSONObject().put("armed", armed)) in 200..299
            ui {
                if (ok) {
                    showArmed(armed)
                } else {
                    setSwitchSilently(!armed)
                    Snackbar.make(requireView(), "Couldn't change occupancy alerts. Check the connection.", Snackbar.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun loadOccupancyArmedState() {
        thread {
            val armed = Api.getObject("/api/occupancy_status")?.takeIf { it.has("armed") }?.optBoolean("armed")
            ui {
                if (armed == null) {
                    b.occupancyStatus.text = "Unknown (offline?)"
                } else {
                    setSwitchSilently(armed)
                    showArmed(armed)
                }
            }
        }
    }

    private fun setSwitchSilently(checked: Boolean) {
        b.occupancySwitch.setOnCheckedChangeListener(null)
        b.occupancySwitch.isChecked = checked
        b.occupancySwitch.setOnCheckedChangeListener { _, isChecked -> setOccupancyArmed(isChecked) }
    }

    private fun showArmed(armed: Boolean) {
        b.occupancyStatus.text = if (armed) "Armed – push on arrival" else "Off"
    }

    // ---------- rendering ----------

    private fun render(s: Snapshot) {
        s.latest?.let { renderLatest(it) }
        s.air?.let { renderPressure(it) }
        s.hourly?.let { renderHourly(it) }
        renderDevices(s.devices, s.latest)
        s.restarts?.let { renderRestarts(it) }
        s.stats?.let { renderStats(it) }
        updateAges()
    }

    private fun renderLatest(latest: JSONObject) {
        // Temperature + humidity from the device with the newest env reading (the BME280
        // moved boards in the node merge, #57). Older backends without env_ts: the main node.
        val main = latest.newestBy("env_ts") ?: latest.optJSONObject(MAIN)
        val temp = main?.optNum("temp")
        val hum = main?.optNum("humidity")
        b.tempValue.text = valueWithUnit(temp?.let { "%.1f".format(Locale.US, it) }, "°C")
        b.humidityValue.text = valueWithUnit(hum?.let { "%.0f".format(Locale.US, it) }, "%")
        Levels.applyChip(b.tempChip, temp?.let(Levels::temperature))
        Levels.applyChip(b.humidityChip, hum?.let(Levels::humidity))
        // env_ts is the env reading's own time; `ts` can be the radar's (contracts/api.md, #11)
        envTs = main?.optTs("env_ts") ?: main?.optTs("ts")

        // CO2 from the device with the newest CO2 reading: the main node since the merge,
        // co2_sensor_esp32 before. `co2` is null whenever the reading isn't valid.
        val co2Node = latest.newestBy("co2_ts") ?: latest.optJSONObject(CO2)
        val ppm = co2Node?.optNum("co2")?.toInt()
        val sensorOk = co2Node?.takeIf { it.has("co2_ok") && !it.isNull("co2_ok") }?.optBoolean("co2_ok")
        b.co2Value.text = ppm?.toString() ?: "--"
        b.co2Gauge.ppm = ppm
        if (ppm != null) {
            val level = Levels.co2(ppm)
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
        co2Ts = co2Node?.optTs("co2_ts") ?: co2Node?.optTs("ts")

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
            // The retired CO2 node only while the backend still lists it (#57)
            if (id == CO2 && devices != null && id !in online) return@forEach
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
        val ctx = requireContext()
        val d = resources.displayMetrics.density
        val card = AccentCard(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding((16 * d).toInt(), (12 * d).toInt(), (16 * d).toInt(), (12 * d).toInt())
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { bottomMargin = (8 * d).toInt() }
        }
        val dot = View(ctx).apply {
            setBackgroundResource(R.drawable.dot)
            backgroundTintList = android.content.res.ColorStateList.valueOf(
                Levels.color(ctx, when (online) { true -> R.color.good; false -> R.color.bad; null -> R.color.faint })
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
        card.addView(dot)
        card.addView(texts)
        return card
    }

    private fun renderRestarts(json: JSONObject) {
        val total24h = json.keys().asSequence().sumOf { json.optJSONObject(it)?.optInt("last_24h") ?: 0 }
        b.restartsValue.text = when (total24h) {
            0 -> "Restarts: none in the last 24 h"
            1 -> "Restarts: 1 in the last 24 h"
            else -> "Restarts: $total24h in the last 24 h"
        }
        b.restartsValue.setTextColor(Levels.color(requireContext(), when {
            total24h == 0 -> R.color.muted
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

    /** API timestamps are ISO 8601 UTC ("2026-10-15T18:42:15Z", #65); the old local format still parses. */
    private fun parseTs(s: String?): Date? {
        if (s.isNullOrEmpty()) return null
        return runCatching { Date.from(Instant.parse(s)) }.getOrNull()
            ?: runCatching { legacyServerTime.parse(s) }.getOrNull()
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
