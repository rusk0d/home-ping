package com.david.homeenv.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View
import com.david.homeenv.R
import java.util.Locale

/**
 * Multi-day chart for the history screen (#82), one column per label (a day, or a
 * month in the 12-month view) with the labels underneath; columns with a blank label
 * (the 30-day view labels every 7th day) get no separator. Two modes:
 * - [setLine]: hourly averages as a line with a soft fill, scaled to their own
 *   min/max (shown on the left); the line breaks where hours have no data.
 * - [setBars]: one value per day as a bar from zero, with the value above it.
 */
class HistoryChartView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val d = resources.displayMetrics.density
    private val plotHeight = 120 * d
    private val axisGap = 4 * d
    private val radius = 4 * d

    private val muted = Levels.color(context, R.color.muted)
    private val faint = Levels.color(context, R.color.faint)

    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2 * d
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
    }
    private val areaPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Levels.withAlpha(faint, 0.6f)
        strokeWidth = 1 * d
    }
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = muted
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 10f, resources.displayMetrics)
    }
    private val line = Path()
    private val area = Path()
    private val rect = RectF()

    private var dayLabels: List<String> = emptyList()
    // Line mode: dayLabels.size * 24 slots, null = no data that hour
    private var slots: List<Double?> = emptyList()
    private var fmt = "%.1f"
    // Bar mode: one value per day, null = no data
    private var bars: List<Double?>? = null
    private var barFmt: (Double) -> String = { it.toString() }

    var color: Int = muted
        set(value) {
            field = value
            linePaint.color = value
            areaPaint.color = Levels.withAlpha(value, 0.16f)
            barPaint.color = Levels.withAlpha(value, 0.85f)
            invalidate()
        }

    init {
        context.obtainStyledAttributes(attrs, R.styleable.AccentCard).apply {
            color = getColor(R.styleable.AccentCard_accentColor, muted)
            recycle()
        }
    }

    /**
     * [hourly]: the line's points, oldest first, spread evenly over the columns (24 per
     * day in the 7-day view, one per column otherwise); [labelFmt] formats the min/max labels.
     */
    fun setLine(days: List<String>, hourly: List<Double?>, labelFmt: String) {
        dayLabels = days
        slots = hourly
        fmt = labelFmt
        bars = null
        invalidate()
    }

    /** One value per day (oldest first); [format] gives the label above each bar. */
    fun setBars(days: List<String>, values: List<Double?>, format: (Double) -> String) {
        dayLabels = days
        bars = values
        barFmt = format
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val h = plotHeight + axisGap + textPaint.textSize + 2 * d
        setMeasuredDimension(
            getDefaultSize(suggestedMinimumWidth, widthMeasureSpec),
            resolveSize(h.toInt(), heightMeasureSpec)
        )
    }

    override fun onDraw(canvas: Canvas) {
        val n = dayLabels.size
        if (n == 0) return
        val dayW = width.toFloat() / n

        // Day separators and labels
        textPaint.textAlign = Paint.Align.CENTER
        val baseline = plotHeight + axisGap - textPaint.ascent()
        dayLabels.forEachIndexed { i, label ->
            if (i > 0 && label.isNotBlank()) canvas.drawLine(i * dayW, 0f, i * dayW, plotHeight, gridPaint)
            canvas.drawText(label, (i + 0.5f) * dayW, baseline, textPaint)
        }
        canvas.drawLine(0f, plotHeight, width.toFloat(), plotHeight, gridPaint)

        val b = bars
        if (b != null) drawBars(canvas, b, dayW) else drawLine(canvas)
    }

    private fun drawBars(canvas: Canvas, values: List<Double?>, dayW: Float) {
        val max = values.filterNotNull().maxOrNull()?.takeIf { it > 0 } ?: return
        val labelH = textPaint.textSize + 4 * d
        val barW = dayW * 0.55f
        textPaint.textAlign = Paint.Align.CENTER
        values.forEachIndexed { i, v ->
            v ?: return@forEachIndexed
            val cx = (i + 0.5f) * dayW
            val top = plotHeight - (plotHeight - labelH) * (v / max).toFloat()
            rect.set(cx - barW / 2, top, cx + barW / 2, plotHeight)
            if (v > 0) canvas.drawRoundRect(rect, radius, radius, barPaint)
            canvas.drawText(barFmt(v), cx, top - 4 * d, textPaint)
        }
    }

    private fun drawLine(canvas: Canvas) {
        val values = slots.filterNotNull()
        if (values.isEmpty()) return
        var min = values.min()
        var max = values.max()
        if (max - min < 1e-9) { min -= 1; max += 1 }

        // Leave room for the min/max labels at the top and bottom
        val pad = textPaint.textSize + 2 * d
        val h = plotHeight - 2 * pad
        fun x(i: Int) = width * (i + 0.5f) / slots.size
        fun y(v: Double) = pad + h * (1 - ((v - min) / (max - min))).toFloat()

        // One segment per run of consecutive hours with data
        line.reset()
        area.reset()
        var runStart = -1
        fun closeRun(end: Int) {
            if (runStart < 0) return
            area.lineTo(x(end), plotHeight)
            area.lineTo(x(runStart), plotHeight)
            area.close()
            runStart = -1
        }
        slots.forEachIndexed { i, v ->
            if (v == null) { closeRun(i - 1); return@forEachIndexed }
            if (runStart < 0) {
                runStart = i
                line.moveTo(x(i), y(v))
                area.moveTo(x(i), y(v))
            } else {
                line.lineTo(x(i), y(v))
                area.lineTo(x(i), y(v))
            }
        }
        closeRun(slots.lastIndex)   // a run still open here ends at the last slot
        canvas.drawPath(area, areaPaint)
        canvas.drawPath(line, linePaint)

        // Lone points (a single hour between gaps) would be invisible as a path
        slots.forEachIndexed { i, v ->
            if (v != null && slots.getOrNull(i - 1) == null && slots.getOrNull(i + 1) == null) {
                canvas.drawCircle(x(i), y(v), linePaint.strokeWidth, barPaint)
            }
        }

        // Min/max of the data on the left, over the plot
        textPaint.textAlign = Paint.Align.LEFT
        canvas.drawText(fmt.format(Locale.US, values.max()), 2 * d, pad - 4 * d, textPaint)
        canvas.drawText(fmt.format(Locale.US, values.min()), 2 * d, plotHeight - 4 * d, textPaint)
    }
}
