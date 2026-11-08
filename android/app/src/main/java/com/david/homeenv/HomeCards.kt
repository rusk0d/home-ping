package com.david.homeenv

import android.content.Context
import android.view.View
import android.widget.TextView
import com.david.homeenv.HomeFormat.valueWithUnit
import com.david.homeenv.databinding.FragmentHomeBinding
import com.david.homeenv.ui.Level
import com.david.homeenv.ui.Levels
import com.david.homeenv.ui.SparklineView
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

// The Home screen's cards, one renderer each (#306, R5), fed from /api/dashboard
// (contracts/api.md). HomeFragment only loads the data and calls them.

/** A node from /api/devices: [name] and [role] ("room" / "fridge") from the backend's registry (#306). */
class Device(val id: String, val name: String, val role: String, val online: Boolean) {
    companion object {
        fun list(devices: JSONArray?): List<Device> = (0 until (devices?.length() ?: 0)).mapNotNull { i ->
            val d = devices?.optJSONObject(i) ?: return@mapNotNull null
            val id = d.optString("device")
            Device(id, d.optString("name").ifEmpty { id }, d.optString("role").ifEmpty { "room" }, d.optBoolean("online"))
        }
    }
}

/** A card: [render] its data, [updateAge] every few seconds (only cards with a reading time). */
abstract class HomeCard(protected val b: FragmentHomeBinding) {
    protected val ctx: Context get() = b.root.context

    open fun updateAge(now: Long) {}

    /** Seconds since [ts], or null. */
    protected fun ageS(ts: Date?, now: Long): Long? = ts?.let { (now - it.time) / 1000 }
}

/** The device entry of /api/latest_all with the newest [tsKey] (env_ts / co2_ts), or null. */
private fun JSONObject.newestBy(tsKey: String, filter: (JSONObject) -> Boolean = { true }): JSONObject? =
    keys().asSequence().mapNotNull { optJSONObject(it) }
        .filter { it.has(tsKey) && !it.isNull(tsKey) && filter(it) }
        .maxByOrNull { it.optString(tsKey) }   // ISO 8601 UTC strings sort by time

/** Temperature and humidity: the room device with the newest env reading. */
class RoomCard(b: FragmentHomeBinding) : HomeCard(b) {
    private var envTs: Date? = null

    fun render(latest: JSONObject, air: JSONObject?) {
        // The BME280 moved boards in the node merge (#57); not the fridge (#290): the backend
        // marks which entries are the room's (`room`, #306)
        val main = latest.newestBy("env_ts") { it.optBoolean("room", true) }
        val temp = main?.optNum("temp")
        val hum = main?.optNum("humidity")
        b.tempValue.text = valueWithUnit(ctx, temp?.let { "%.1f".format(Locale.US, it) }, "°C")
        b.humidityValue.text = valueWithUnit(ctx, hum?.let { "%.0f".format(Locale.US, it) }, "%")
        Levels.applyChip(b.tempChip, temp?.let(Levels::temperature))
        // Humid for hours (/api/air mould_risk, #177): the chip says so instead of the level
        val mould = air?.optBoolean("mould_risk") == true
        Levels.applyChip(b.humidityChip, if (mould && hum != null) Levels.MOULD_RISK else hum?.let(Levels::humidity))
        val dew = if (temp != null && hum != null) Levels.dewPoint(temp, hum) else null
        b.humidityDew.text = dew?.let { "dew %.0f°C".format(Locale.US, it) } ?: ""
        // env_ts is the env reading's own time; `ts` can be the radar's (contracts/api.md, #11)
        envTs = main?.optTs("env_ts")
    }

    override fun updateAge(now: Long) {
        val age = ageS(envTs, now)
        b.tempAge.text = HomeFormat.age(age)
        b.humidityAge.text = HomeFormat.age(age)
        val stale = age != null && age > STALE_S
        b.cardTemp.stale = stale
        b.cardHum.stale = stale
    }

    private companion object {
        // Seconds without a new reading before the card is dimmed (~3 missed publishes, as on the web)
        const val STALE_S = 90
    }
}

/** CO₂: the device with the newest CO₂ reading (the main node since the merge, #57). */
class Co2Card(b: FragmentHomeBinding) : HomeCard(b) {
    private var co2Ts: Date? = null

    fun render(latest: JSONObject, air: JSONObject?) {
        val node = latest.newestBy("co2_ts")
        // `co2` is null whenever the reading isn't valid
        val ppm = node?.optNum("co2")?.toInt()
        val sensorOk = node?.takeIf { it.has("co2_ok") && !it.isNull("co2_ok") }?.optBoolean("co2_ok")
        b.co2Value.text = ppm?.toString() ?: "--"
        b.co2Gauge.value = ppm?.toDouble()
        if (ppm != null) {
            // The server's band (/api/air), when it is about this same reading (#127)
            val serverBand = air?.takeIf { it.optNum("co2")?.toInt() == ppm }?.optString("band")
            val level = Levels.co2Band(serverBand) ?: Levels.co2(ppm)
            Levels.applyChip(b.co2Chip, level)
            b.cardCo2.accentColor = Levels.color(ctx, level.color)
        } else {
            Levels.applyChip(b.co2Chip, if (node != null) Level("No valid reading", R.color.ok) else null)
            b.cardCo2.accentColor = Levels.color(ctx, R.color.faint)
        }
        b.co2SensorStatus.text = when (sensorOk) {
            true -> "sensor OK"
            false -> "sensor fault / warming up"
            null -> "sensor --"
        }
        co2Ts = node?.optTs("co2_ts")
    }

    override fun updateAge(now: Long) {
        val age = ageS(co2Ts, now)
        b.co2Age.text = HomeFormat.age(age)
        b.cardCo2.stale = age != null && age > STALE_S
    }

    private companion object {
        const val STALE_S = 180
    }
}

/** Occupied now: the combined radar state. */
class OccupancyNowCard(b: FragmentHomeBinding) : HomeCard(b) {
    fun render(latest: JSONObject) {
        val combined = latest.optJSONObject("combined") ?: return
        // Both null when no radar reported recently
        val occupied = combined.optNum("occupied")?.let { it == 1.0 }
        b.occupiedValue.text = when (occupied) { true -> "Yes"; false -> "No"; null -> "--" }
        Levels.applyChip(b.occupiedChip, when (occupied) {
            true -> Level("Occupied", R.color.good)
            false -> Level("Vacant", R.color.muted)
            null -> null
        })
        b.cardOccNow.accentColor = Levels.color(ctx, if (occupied == true) R.color.good else R.color.faint)
        b.confidenceValue.text = "confidence " + (combined.optNum("confidence")?.let { "%.0f%%".format(Locale.US, it) } ?: "--")
    }
}

/** The fridge (#290): the node with role "fridge" in /api/devices (#306). */
class FridgeCard(b: FragmentHomeBinding) : HomeCard(b) {
    private var fridgeTs: Date? = null

    init {
        b.fridgeGauge.scale = Levels.FRIDGE
    }

    fun render(latest: JSONObject, devices: List<Device>) {
        val fridge = devices.firstOrNull { it.role == "fridge" }?.let { latest.optJSONObject(it.id) }
        val temp = fridge?.optNum("temp")
        // optString() turns a JSON null into "null"
        val alert = fridge?.takeUnless { it.isNull("alert") }?.optString("alert").orEmpty()
        b.fridgeValue.text = valueWithUnit(ctx, temp?.let { "%.1f".format(Locale.US, it) }, "°C")
        Levels.applyChip(b.fridgeAlert, temp?.let { Levels.fridge(alert) })
        b.fridgeGauge.value = temp
        // The door left open (#361, the backend's door push #336): it leads the status, in red
        val doorOpen = fridge?.optBoolean("door_open") == true
        val door = if (!doorOpen) null else "door open?" + (fridge?.optTs("door_open_since")
            ?.let { " since " + SimpleDateFormat("HH:mm", Locale.getDefault()).format(it) } ?: "")
        b.fridgeStatus.text = when {
            fridge == null || temp == null -> "no data"
            door != null -> listOf(door, status(alert)).filter { it != "normal" }.joinToString(" · ")
            else -> status(alert)
        }
        b.fridgeStatus.setTextColor(Levels.color(ctx, if (door != null) R.color.bad else R.color.muted))
        fridgeTs = fridge?.optTs("ts")
    }

    /**
     * Today's lowest and highest (#332): the extremes of /api/hourly's `min_fridge` /
     * `max_fridge` (#331), so a short spike shows even when the hour's average hides it.
     */
    fun renderToday(hourly: JSONArray) {
        val items = (0 until hourly.length()).mapNotNull { hourly.optJSONObject(it) }
        val lo = items.mapNotNull { it.optNum("min_fridge") }.minOrNull()
        val hi = items.mapNotNull { it.optNum("max_fridge") }.maxOrNull()
        b.fridgeRange.visibility = if (lo != null && hi != null) View.VISIBLE else View.GONE
        if (lo != null && hi != null) b.fridgeRange.text = "today %.1f–%.1f°".format(Locale.US, lo, hi)
    }

    private fun status(alert: String) = when (alert) {
        "" -> "normal"
        "cold" -> "below 0 °C for 30+ min"
        "warm_low" -> "above 5 °C for 30+ min"
        "warm_critical" -> "above 8 °C for 30+ min"
        "warm_critical_duration" -> "above 5 °C for 2+ h"
        else -> alert
    }

    override fun updateAge(now: Long) {
        val age = ageS(fridgeTs, now)
        b.fridgeAge.text = HomeFormat.age(age)
        b.cardFridge.stale = age != null && age > STALE_S
    }

    private companion object {
        const val STALE_S = 120
    }
}

/** Pressure and its 3 h trend (/api/air). */
class PressureCard(b: FragmentHomeBinding) : HomeCard(b) {
    fun render(air: JSONObject) {
        val pressure = air.optNum("pressure")
        b.pressureValue.text = valueWithUnit(ctx, pressure?.let { "%.0f".format(Locale.US, it) }, "hPa")
        val delta = air.optNum("pressure_3h_change")
        Levels.applyChip(b.pressureChip, delta?.let(Levels::pressureTrend))
        b.pressureTrend.text = delta?.let { "3 h: %+.1f hPa".format(Locale.US, it) } ?: "3 h: not enough data"
    }
}

/** Today's hours (/api/hourly): the temperature and humidity sparklines and the occupancy bars. */
class HourlyCard(b: FragmentHomeBinding) : HomeCard(b) {
    fun render(hourly: JSONArray) {
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
        spark(b.tempSpark, b.tempRange, temps.values.toList(), "%.1f", "°")
        spark(b.humiditySpark, b.humidityRange, hums.values.toList(), "%.0f", "%")
        b.occupancyBars.setData(byHour, current)

        val total = byHour.values.sum() / 100.0
        val peak = byHour.filterValues { it > 0 }.maxByOrNull { it.value }
        b.occSummary.text = buildString {
            append("≈ %.1f h occupied".format(Locale.US, total))
            if (peak != null) append(" · busiest %02d:00".format(peak.key))
        }
    }

    private fun spark(spark: SparklineView, range: TextView, values: List<Double>, fmt: String, unit: String) {
        spark.setPoints(values)
        range.text = if (values.size < 2) "" else
            "today ${fmt.format(Locale.US, values.min())}–${fmt.format(Locale.US, values.max())}$unit"
    }
}

/** The last 24 h averages (/api/stats24h_combined). */
class StatsCard(b: FragmentHomeBinding) : HomeCard(b) {
    fun render(stats: JSONObject) {
        b.statsAvgTemp.text = valueWithUnit(ctx, stats.optNum("avg_temp")?.let { "%.1f".format(Locale.US, it) }, "°C")
        b.statsAvgHumidity.text = valueWithUnit(ctx, stats.optNum("avg_humidity")?.let { "%.0f".format(Locale.US, it) }, "%")
        b.statsAvgOccupancy.text = valueWithUnit(ctx, stats.optNum("avg_occupancy")?.let { "%.0f".format(Locale.US, it) }, "%")
        b.statsOccupiedHours.text = stats.optNum("occupied_hours")?.let { HomeFormat.hours(it) } ?: "--"
    }
}
