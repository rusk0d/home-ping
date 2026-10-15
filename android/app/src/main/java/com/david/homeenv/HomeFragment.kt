package com.david.homeenv

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.util.Base64
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.david.homeenv.ui.AccentCard
import com.david.homeenv.ui.Co2GaugeView
import com.david.homeenv.ui.Level
import com.david.homeenv.ui.Levels
import com.david.homeenv.ui.OccupancyBarsView
import com.david.homeenv.ui.SparklineView
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.switchmaterial.SwitchMaterial
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

// Home screen, styled after the web dashboard (#30). Endpoints: contracts/api.md
class HomeFragment : Fragment() {

    companion object {
        private val client = OkHttpClient()
        private val authHeader = "Basic " + Base64.encodeToString(
            "${BuildConfig.API_USER}:${BuildConfig.API_PASS}".toByteArray(), Base64.NO_WRAP
        )
        private const val REFRESH_MS = 60_000L
        private const val AGE_TICK_MS = 10_000L
        // Seconds without a new reading before a card is dimmed (~3 missed publishes, as on the web)
        private const val STALE_ENV_S = 90
        private const val STALE_CO2_S = 180

        private const val MAIN = "home_ping_esp32"
        private const val HALLWAY = "hallway_presence_esp32"
        private const val CO2 = "co2_sensor_esp32"
        private val DEVICE_NAMES = linkedMapOf(MAIN to "Main node", HALLWAY to "Hallway radar", CO2 to "CO₂ node")
    }

    private fun url(path: String) = "${BuildConfig.API_BASE_URL}$path"

    private val handler = Handler(Looper.getMainLooper())
    private val serverTime = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)

    // Timestamps of the latest readings, for the "x ago" labels and stale dimming
    private var envTs: Date? = null
    private var co2Ts: Date? = null

    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var occupancySwitch: SwitchMaterial
    private lateinit var occupancyStatus: TextView
    private lateinit var cardTemp: AccentCard
    private lateinit var cardHum: AccentCard
    private lateinit var cardPres: AccentCard
    private lateinit var cardOccNow: AccentCard
    private lateinit var cardCo2: AccentCard
    private lateinit var tempValue: TextView
    private lateinit var tempChip: TextView
    private lateinit var tempAge: TextView
    private lateinit var tempSpark: SparklineView
    private lateinit var tempRange: TextView
    private lateinit var humidityValue: TextView
    private lateinit var humidityChip: TextView
    private lateinit var humidityAge: TextView
    private lateinit var humiditySpark: SparklineView
    private lateinit var humidityRange: TextView
    private lateinit var pressureValue: TextView
    private lateinit var pressureChip: TextView
    private lateinit var pressureTrend: TextView
    private lateinit var occupiedValue: TextView
    private lateinit var occupiedChip: TextView
    private lateinit var confidenceValue: TextView
    private lateinit var co2Value: TextView
    private lateinit var co2Chip: TextView
    private lateinit var co2Gauge: Co2GaugeView
    private lateinit var co2SensorStatus: TextView
    private lateinit var co2Age: TextView
    private lateinit var occSummary: TextView
    private lateinit var occBars: OccupancyBarsView
    private lateinit var occLegend: TextView
    private lateinit var devicesList: LinearLayout
    private lateinit var restartsValue: TextView
    private lateinit var statsAvgTemp: TextView
    private lateinit var statsAvgHumidity: TextView
    private lateinit var statsAvgOccupancy: TextView
    private lateinit var statsOccupiedHours: TextView

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

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View? =
        inflater.inflate(R.layout.fragment_home, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        swipeRefresh = view.findViewById(R.id.swipeRefresh)
        occupancySwitch = view.findViewById(R.id.occupancySwitch)
        occupancyStatus = view.findViewById(R.id.occupancyStatus)
        cardTemp = view.findViewById(R.id.card_temp)
        cardHum = view.findViewById(R.id.card_hum)
        cardPres = view.findViewById(R.id.card_pres)
        cardOccNow = view.findViewById(R.id.card_occ_now)
        cardCo2 = view.findViewById(R.id.card_co2)
        tempValue = view.findViewById(R.id.temp_value)
        tempChip = view.findViewById(R.id.temp_chip)
        tempAge = view.findViewById(R.id.temp_age)
        tempSpark = view.findViewById(R.id.temp_spark)
        tempRange = view.findViewById(R.id.temp_range)
        humidityValue = view.findViewById(R.id.humidity_value)
        humidityChip = view.findViewById(R.id.humidity_chip)
        humidityAge = view.findViewById(R.id.humidity_age)
        humiditySpark = view.findViewById(R.id.humidity_spark)
        humidityRange = view.findViewById(R.id.humidity_range)
        pressureValue = view.findViewById(R.id.pressure_value)
        pressureChip = view.findViewById(R.id.pressure_chip)
        pressureTrend = view.findViewById(R.id.pressure_trend)
        occupiedValue = view.findViewById(R.id.occupied_value)
        occupiedChip = view.findViewById(R.id.occupied_chip)
        confidenceValue = view.findViewById(R.id.confidence_value)
        co2Value = view.findViewById(R.id.co2_value)
        co2Chip = view.findViewById(R.id.co2_chip)
        co2Gauge = view.findViewById(R.id.co2_gauge)
        co2SensorStatus = view.findViewById(R.id.co2_sensor_status)
        co2Age = view.findViewById(R.id.co2_age)
        occSummary = view.findViewById(R.id.occ_summary)
        occBars = view.findViewById(R.id.occupancy_bars)
        occLegend = view.findViewById(R.id.occ_legend)
        devicesList = view.findViewById(R.id.devices_list)
        restartsValue = view.findViewById(R.id.restarts_value)
        statsAvgTemp = view.findViewById(R.id.stats_avg_temp)
        statsAvgHumidity = view.findViewById(R.id.stats_avg_humidity)
        statsAvgOccupancy = view.findViewById(R.id.stats_avg_occupancy)
        statsOccupiedHours = view.findViewById(R.id.stats_occupied_hours)

        occLegend.text = buildLegend()
        swipeRefresh.setOnRefreshListener { loadDashboardData() }
        occupancySwitch.setOnCheckedChangeListener { _, isChecked -> setOccupancyArmed(isChecked) }
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

    // ---------- networking ----------

    private fun get(path: String): String? = try {
        val request = Request.Builder().url(url(path)).addHeader("Authorization", authHeader).build()
        client.newCall(request).execute().use { if (it.isSuccessful) it.body?.string() else null }
    } catch (e: Exception) {
        e.printStackTrace()
        null
    }

    // Unexpected bodies (e.g. an HTML error page) become null instead of crashing the thread
    private fun getObject(path: String): JSONObject? = get(path)?.let { runCatching { JSONObject(it) }.getOrNull() }
    private fun getArray(path: String): JSONArray? = get(path)?.let { runCatching { JSONArray(it) }.getOrNull() }

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
                latest = getObject("/api/latest_all"),
                devices = getArray("/api/devices"),
                stats = getObject("/api/stats24h_combined"),
                restarts = getObject("/api/restarts"),
                air = getObject("/api/air"),
                hourly = getArray("/api/hourly"),
            )
            ui {
                try {
                    render(snap)
                } catch (e: Exception) {
                    // Never crash the screen on an unexpected payload
                    e.printStackTrace()
                }
                swipeRefresh.isRefreshing = false
            }
        }
    }

    private fun setOccupancyArmed(armed: Boolean) {
        thread {
            val ok = try {
                val body = JSONObject().put("armed", armed).toString()
                    .toRequestBody("application/json; charset=utf-8".toMediaType())
                val request = Request.Builder().url(url("/api/occupancy_arm")).post(body)
                    .addHeader("Authorization", authHeader).build()
                client.newCall(request).execute().use { it.isSuccessful }
            } catch (e: Exception) {
                e.printStackTrace()
                false
            }
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
            val armed = getObject("/api/occupancy_status")?.takeIf { it.has("armed") }?.optBoolean("armed")
            ui {
                if (armed == null) {
                    occupancyStatus.text = "Unknown (offline?)"
                } else {
                    setSwitchSilently(armed)
                    showArmed(armed)
                }
            }
        }
    }

    private fun setSwitchSilently(checked: Boolean) {
        occupancySwitch.setOnCheckedChangeListener(null)
        occupancySwitch.isChecked = checked
        occupancySwitch.setOnCheckedChangeListener { _, isChecked -> setOccupancyArmed(isChecked) }
    }

    private fun showArmed(armed: Boolean) {
        occupancyStatus.text = if (armed) "Armed – push on arrival" else "Off"
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
        // Temperature + humidity from the main node (BME280). Fields can be null.
        val main = latest.optJSONObject(MAIN)
        val temp = main?.optNum("temp")
        val hum = main?.optNum("humidity")
        tempValue.text = valueWithUnit(temp?.let { "%.1f".format(Locale.US, it) }, "°C")
        humidityValue.text = valueWithUnit(hum?.let { "%.0f".format(Locale.US, it) }, "%")
        Levels.applyChip(tempChip, temp?.let(Levels::temperature))
        Levels.applyChip(humidityChip, hum?.let(Levels::humidity))
        // Note: for the main node, `ts` is the radar's timestamp (see contracts/api.md)
        envTs = main?.optTs("ts")

        // CO2 node: `co2` is null whenever the reading isn't valid
        val co2Node = latest.optJSONObject(CO2)
        val ppm = co2Node?.optNum("co2")?.toInt()
        val sensorOk = co2Node?.takeIf { it.has("co2_ok") && !it.isNull("co2_ok") }?.optBoolean("co2_ok")
        co2Value.text = ppm?.toString() ?: "--"
        co2Gauge.ppm = ppm
        if (ppm != null) {
            val level = Levels.co2(ppm)
            Levels.applyChip(co2Chip, level)
            cardCo2.accentColor = Levels.color(requireContext(), level.color)
        } else {
            Levels.applyChip(co2Chip, if (co2Node != null) Level("No valid reading", R.color.ok) else null)
            cardCo2.accentColor = Levels.color(requireContext(), R.color.faint)
        }
        co2SensorStatus.text = when (sensorOk) {
            true -> "sensor OK"
            false -> "sensor fault / warming up"
            null -> "sensor --"
        }
        co2Ts = co2Node?.optTs("ts")

        // Occupied now: combined radar state
        latest.optJSONObject("combined")?.let { combined ->
            // Both null when no radar reported recently
            val occupied = combined.optNum("occupied")?.let { it == 1.0 }
            occupiedValue.text = when (occupied) { true -> "Yes"; false -> "No"; null -> "--" }
            Levels.applyChip(occupiedChip, when (occupied) {
                true -> Level("Occupied", R.color.good)
                false -> Level("Vacant", R.color.muted)
                null -> null
            })
            cardOccNow.accentColor = Levels.color(requireContext(), if (occupied == true) R.color.good else R.color.faint)
            confidenceValue.text = "confidence " + (combined.optNum("confidence")?.let { "%.0f%%".format(Locale.US, it) } ?: "--")
        }
    }

    private fun renderPressure(air: JSONObject) {
        val pressure = air.optNum("pressure")
        pressureValue.text = valueWithUnit(pressure?.let { "%.0f".format(Locale.US, it) }, "hPa")
        val delta = air.optNum("pressure_3h_change")
        Levels.applyChip(pressureChip, delta?.let(Levels::pressureTrend))
        pressureTrend.text = delta?.let { "3 h: %+.1f hPa".format(Locale.US, it) } ?: "3 h: not enough data"
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
        renderSpark(tempSpark, tempRange, temps.values.toList(), "%.1f", "°")
        renderSpark(humiditySpark, humidityRange, hums.values.toList(), "%.0f", "%")
        occBars.setData(byHour, current)

        val total = byHour.values.sum() / 100.0
        val peak = byHour.filterValues { it > 0 }.maxByOrNull { it.value }
        occSummary.text = buildString {
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
        devicesList.removeAllViews()
        DEVICE_NAMES.forEach { (id, name) ->
            val uptime = latest?.optJSONObject(id)?.optNum("uptime")?.toInt()
            val meta = when {
                id !in online && uptime == null -> "no data yet"
                else -> listOfNotNull(
                    if (online[id] == true) "online" else "offline",
                    uptime?.let { "up ${formatUptime(it)}" },
                ).joinToString(" · ")
            }
            devicesList.addView(deviceRow(name, meta, online[id]))
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
        restartsValue.text = when (total24h) {
            0 -> "Restarts: none in the last 24 h"
            1 -> "Restarts: 1 in the last 24 h"
            else -> "Restarts: $total24h in the last 24 h"
        }
        restartsValue.setTextColor(Levels.color(requireContext(), when {
            total24h == 0 -> R.color.muted
            total24h <= 2 -> R.color.ok
            else -> R.color.bad
        }))
    }

    private fun renderStats(stats: JSONObject) {
        statsAvgTemp.text = valueWithUnit(stats.optNum("avg_temp")?.let { "%.1f".format(Locale.US, it) }, "°C")
        statsAvgHumidity.text = valueWithUnit(stats.optNum("avg_humidity")?.let { "%.0f".format(Locale.US, it) }, "%")
        statsAvgOccupancy.text = valueWithUnit(stats.optNum("avg_occupancy")?.let { "%.0f".format(Locale.US, it) }, "%")
        statsOccupiedHours.text = stats.optNum("occupied_hours")?.let { formatHours(it) } ?: "--"
    }

    // ---------- ages / staleness ----------

    private fun updateAges() {
        if (view == null) return
        val now = System.currentTimeMillis()
        val envAge = envTs?.let { (now - it.time) / 1000 }
        val co2AgeS = co2Ts?.let { (now - it.time) / 1000 }
        tempAge.text = formatAge(envAge)
        humidityAge.text = formatAge(envAge)
        co2Age.text = formatAge(co2AgeS)
        val envStale = envAge != null && envAge > STALE_ENV_S
        cardTemp.stale = envStale
        cardHum.stale = envStale
        cardCo2.stale = co2AgeS != null && co2AgeS > STALE_CO2_S
    }

    // ---------- helpers ----------

    private fun JSONObject.optNum(key: String): Double? =
        if (has(key) && !isNull(key)) optDouble(key).takeIf { !it.isNaN() } else null

    private fun JSONObject.optTs(key: String): Date? = if (isNull(key)) null else parseTs(optString(key))

    private fun parseTs(s: String?): Date? = try {
        s?.let { serverTime.parse(it) }
    } catch (e: Exception) {
        null
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
