package com.david.homeenv

import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.david.homeenv.databinding.ActivityHistoryBinding
import com.david.homeenv.ui.HistoryChartView
import com.david.homeenv.ui.Level
import com.david.homeenv.ui.Levels
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.YearMonth
import java.time.temporal.ChronoUnit
import java.time.format.TextStyle
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * History screen (#82): one chart per metric over a selectable range (#129):
 * - 7 days: hourly averages from `/api/hourly?days=7`
 * - 30 days: daily averages from `/api/daily?days=30`
 * - 12 months: monthly averages of the `/api/daily` days (contracts/api.md)
 */
class HistoryActivity : AppCompatActivity() {

    private enum class Range(val subtitle: String) {
        WEEK("Last 7 days, hourly averages"),
        MONTH("Last 30 days, daily averages"),
        YEAR("Last 12 months, monthly averages"),
    }

    companion object {
        private const val DAYS = 7
        private const val MONTH_DAYS = 30
        private const val KEY_RANGE = "range"
    }

    private lateinit var b: ActivityHistoryBinding
    private var range = Range.WEEK
    private var loadJob: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityHistoryBinding.inflate(layoutInflater)
        setContentView(b.root)
        b.back.setOnClickListener { finish() }
        b.swipeRefresh.setOnRefreshListener { load() }
        range = savedInstanceState?.getString(KEY_RANGE)
            ?.let { runCatching { Range.valueOf(it) }.getOrNull() } ?: Range.WEEK
        for ((view, r) in rangeViews()) view.setOnClickListener { if (range != r) { range = r; clear(); load() } }
        load()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(KEY_RANGE, range.name)
    }

    /** Empty charts while another range loads, so a failed load doesn't leave the old range's data. */
    private fun clear() {
        for (chart in listOf(b.tempChart, b.humidityChart, b.co2Chart, b.pressureChart)) chart.setLine(emptyList(), emptyList(), "")
        b.occupancyChart.setBars(emptyList(), emptyList()) { "" }
        for (summary in listOf(b.tempSummary, b.humiditySummary, b.co2Summary, b.pressureSummary, b.occupancySummary)) summary.text = ""
    }

    private fun rangeViews() = listOf(b.rangeWeek to Range.WEEK, b.rangeMonth to Range.MONTH, b.rangeYear to Range.YEAR)

    private fun load() {
        for ((view, r) in rangeViews()) {
            Levels.applyChip(view, Level(view.text.toString(), if (r == range) R.color.good else R.color.muted))
        }
        b.subtitle.text = range.subtitle
        b.swipeRefresh.isRefreshing = true
        val r = range
        // A newer load (another range picked) replaces this one; cancelled with the activity too
        loadJob?.cancel()
        loadJob = lifecycleScope.launch {
            val path = when (r) {
                Range.WEEK -> "/api/hourly?days=$DAYS"
                Range.MONTH -> "/api/daily?days=$MONTH_DAYS"
                // From the 1st of the month 11 months ago, so the oldest month is complete
                Range.YEAR -> "/api/daily?days=${ChronoUnit.DAYS.between(YearMonth.now().minusMonths(11).atDay(1), LocalDate.now()) + 1}"
            }
            val data = withContext(Dispatchers.IO) { Api.getArray(path) }
            b.swipeRefresh.isRefreshing = false
            b.error.visibility = if (data == null) View.VISIBLE else View.GONE
            data ?: return@launch
            when (r) {
                Range.WEEK -> renderHourly(data)
                Range.MONTH -> renderMonth(data)
                Range.YEAR -> renderYear(data)
            }
        }
    }

    private fun renderHourly(hourly: JSONArray) {
        // Buckets are labelled in the server's local time ("YYYY-MM-DD HH:00"), newest
        // first. The last day is the newest bucket's date (as on the home screen), so
        // the days line up with the server even if the phone is in another timezone.
        val newest = hourly.optJSONObject(0)?.optString("hour")?.let(::parseDate) ?: LocalDate.now()
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

    /** 30 days, one point per day; a date label every 7 days, ending with today. */
    private fun renderMonth(daily: JSONArray) {
        val byDate = byDate(daily)
        val newest = byDate.keys.maxOrNull() ?: LocalDate.now()
        val days = (MONTH_DAYS - 1 downTo 0).map { newest.minusDays(it.toLong()) }
        val labels = days.mapIndexed { i, day ->
            when {
                day == newest -> "Today"
                (days.lastIndex - i) % 7 == 0 -> "${day.month.getDisplayName(TextStyle.SHORT, Locale.US)} ${day.dayOfMonth}"
                else -> ""
            }
        }
        fun series(key: String) = days.map { byDate[it]?.optNum(key) }

        line(b.tempChart, b.tempSummary, labels, series("avg_temp"), "%.1f", "°C",
            series("min_temp"), series("max_temp"))
        line(b.humidityChart, b.humiditySummary, labels, series("avg_humidity"), "%.0f", "%",
            series("min_humidity"), series("max_humidity"))
        line(b.co2Chart, b.co2Summary, labels, series("avg_co2"), "%.0f", " ppm",
            null, series("max_co2"))
        line(b.pressureChart, b.pressureSummary, labels, series("avg_pressure"), "%.0f", " hPa")

        val occ = series("occupied_hours")
        b.occupancyChart.setBars(labels, occ) { "" }   // 30 labels would overlap; the summary has the numbers
        occupancySummary(occ) { i -> "${days[i].month.getDisplayName(TextStyle.SHORT, Locale.US)} ${days[i].dayOfMonth}" }
    }

    /** 12 months, one point per month: the mean of its days (each day weighs the same). */
    private fun renderYear(daily: JSONArray) {
        val byDate = byDate(daily)
        val newest = YearMonth.from(byDate.keys.maxOrNull() ?: LocalDate.now())
        val months = (11 downTo 0).map { newest.minusMonths(it.toLong()) }
        val labels = months.map { it.month.getDisplayName(TextStyle.SHORT, Locale.US) }
        val byMonth = byDate.entries.groupBy({ YearMonth.from(it.key) }, { it.value })
        val items = months.map { byMonth[it].orEmpty() }

        fun series(key: String) = items.map { days -> days.mapNotNull { it.optNum(key) }.takeIf { it.isNotEmpty() }?.average() }
        // Min/max per month: the extremes of its days, so the summary shows the real range
        fun extreme(key: String, pick: (List<Double>) -> Double) =
            items.map { days -> days.mapNotNull { it.optNum(key) }.takeIf { it.isNotEmpty() }?.let(pick) }

        line(b.tempChart, b.tempSummary, labels, series("avg_temp"), "%.1f", "°C",
            extreme("min_temp") { it.min() }, extreme("max_temp") { it.max() })
        line(b.humidityChart, b.humiditySummary, labels, series("avg_humidity"), "%.0f", "%",
            extreme("min_humidity") { it.min() }, extreme("max_humidity") { it.max() })
        line(b.co2Chart, b.co2Summary, labels, series("avg_co2"), "%.0f", " ppm",
            null, extreme("max_co2") { it.max() })
        line(b.pressureChart, b.pressureSummary, labels, series("avg_pressure"), "%.0f", " hPa")

        // Occupancy: average occupied hours per day with radar data, per month
        val occ = series("occupied_hours")
        b.occupancyChart.setBars(labels, occ) { "%.1f".format(Locale.US, it) }
        occupancySummary(occ) { i -> labels[i] }
    }

    /** "≈ X h a day on average · busiest <name>" for the 30-day and 12-month views. */
    private fun occupancySummary(hoursPerDay: List<Double?>, name: (Int) -> String) {
        val known = hoursPerDay.withIndex().filter { it.value != null }
        val busiest = known.filter { it.value!! > 0 }.maxByOrNull { it.value!! }
        b.occupancySummary.text = if (known.isEmpty()) "No radar data" else buildString {
            append("≈ %.1f h a day on average".format(Locale.US, known.map { it.value!! }.average()))
            if (busiest != null) append(" · busiest ${name(busiest.index)}")
        }
    }

    /** /api/daily entries by their date. */
    private fun byDate(daily: JSONArray): Map<LocalDate, JSONObject> = buildMap {
        for (i in 0 until daily.length()) {
            val item = daily.optJSONObject(i) ?: continue
            parseDate(item.optString("date"))?.let { put(it, item) }
        }
    }

    private fun parseDate(s: String): LocalDate? = runCatching { LocalDate.parse(s.take(10)) }.getOrNull()

    /**
     * Line chart plus its summary "min–max · avg". [mins]/[maxes]: the real extremes per
     * point when known (daily/monthly views); otherwise the extremes of the plotted values.
     * With only [maxes] (CO₂: /api/daily has no daily minimum) it is "avg · peak", and a
     * range that rounds to one value (e.g. a single month) shows just the average.
     */
    private fun line(
        chart: HistoryChartView, summary: TextView, labels: List<String>,
        slots: List<Double?>, fmt: String, unit: String,
        mins: List<Double?>? = null, maxes: List<Double?>? = null,
    ) {
        chart.setLine(labels, slots, fmt)
        val values = slots.filterNotNull()
        if (values.isEmpty()) { summary.text = "No data"; return }
        fun f(v: Double) = fmt.format(Locale.US, v) + unit
        val avg = "avg ${f(values.average())}"
        val peak = maxes?.filterNotNull()?.maxOrNull()
        if (mins == null && peak != null) { summary.text = "$avg · peak ${f(peak)}"; return }
        val lo = mins?.filterNotNull()?.minOrNull() ?: values.min()
        val hi = peak ?: values.max()
        summary.text = if (f(lo) == f(hi)) avg else
            "${fmt.format(Locale.US, lo)}–${f(hi)} · $avg"
    }
}
