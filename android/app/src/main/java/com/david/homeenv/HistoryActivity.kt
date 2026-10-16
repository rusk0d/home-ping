package com.david.homeenv

import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.david.homeenv.databinding.ActivityHistoryBinding
import com.david.homeenv.ui.HistoryChartView
import org.json.JSONArray
import java.time.LocalDate
import java.time.format.TextStyle
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * History screen (#82): the last [DAYS] days of hourly averages from
 * `/api/hourly?days=7` (contracts/api.md), one chart per metric.
 */
class HistoryActivity : AppCompatActivity() {

    companion object {
        private const val DAYS = 7
    }

    private lateinit var b: ActivityHistoryBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityHistoryBinding.inflate(layoutInflater)
        setContentView(b.root)
        b.back.setOnClickListener { finish() }
        b.swipeRefresh.setOnRefreshListener { load() }
        load()
    }

    private fun load() {
        b.swipeRefresh.isRefreshing = true
        // Cancelled with the activity, so no isDestroyed check is needed (#125)
        lifecycleScope.launch {
            val hourly = withContext(Dispatchers.IO) { Api.getArray("/api/hourly?days=$DAYS") }
            b.swipeRefresh.isRefreshing = false
            b.error.visibility = if (hourly == null) View.VISIBLE else View.GONE
            hourly?.let { render(it) }
        }
    }

    private fun render(hourly: JSONArray) {
        // Buckets are labelled in the server's local time ("YYYY-MM-DD HH:00"), newest
        // first. The last day is the newest bucket's date (as on the home screen), so
        // the days line up with the server even if the phone is in another timezone.
        val newest = hourly.optJSONObject(0)?.optString("hour")
            ?.let { runCatching { LocalDate.parse(it.take(10)) }.getOrNull() }
            ?: LocalDate.now()
        val days = (DAYS - 1 downTo 0).map { newest.minusDays(it.toLong()) }
        val labels = days.map { if (it == newest) "Today" else it.dayOfWeek.getDisplayName(TextStyle.SHORT, Locale.US) }
        val dayIndex = days.withIndex().associate { (i, day) -> day.toString() to i }

        // One slot per hour of the window; null = no data
        fun series(key: String): List<Double?> {
            val slots = MutableList<Double?>(DAYS * 24) { null }
            for (i in 0 until hourly.length()) {
                val item = hourly.optJSONObject(i) ?: continue
                val hour = item.optString("hour")
                if (hour.length < 13) continue
                val day = dayIndex[hour.take(10)] ?: continue
                val h = hour.substring(11, 13).toIntOrNull() ?: continue
                slots[day * 24 + h] = item.optNum(key)
            }
            return slots
        }

        line(b.tempChart, b.tempSummary, labels, series("avg_temp"), "%.1f", "°C")
        line(b.humidityChart, b.humiditySummary, labels, series("avg_humidity"), "%.0f", "%")
        line(b.co2Chart, b.co2Summary, labels, series("avg_co2"), "%.0f", " ppm")
        line(b.pressureChart, b.pressureSummary, labels, series("avg_pressure"), "%.0f", " hPa")

        // Occupancy: hours occupied per day = sum of the hourly percentages / 100
        val occ = series("avg_occupancy").chunked(24).map { day ->
            day.filterNotNull().takeIf { it.isNotEmpty() }?.sum()?.div(100)
        }
        b.occupancyChart.setBars(labels, occ) { "%.1fh".format(Locale.US, it) }
        val known = occ.withIndex().filter { it.value != null }
        val busiest = known.filter { it.value!! > 0 }.maxByOrNull { it.value!! }
        b.occupancySummary.text = if (known.isEmpty()) "No radar data" else buildString {
            append("≈ %.1f h in %d days".format(Locale.US, known.sumOf { it.value!! }, known.size))
            if (busiest != null) append(" · busiest ${labels[busiest.index]}")
        }
    }

    private fun line(
        chart: HistoryChartView, summary: TextView, labels: List<String>,
        slots: List<Double?>, fmt: String, unit: String,
    ) {
        chart.setLine(labels, slots, fmt)
        val values = slots.filterNotNull()
        summary.text = if (values.isEmpty()) "No data" else
            "${fmt.format(Locale.US, values.min())}–${fmt.format(Locale.US, values.max())}$unit" +
                " · avg ${fmt.format(Locale.US, values.average())}$unit"
    }
}
