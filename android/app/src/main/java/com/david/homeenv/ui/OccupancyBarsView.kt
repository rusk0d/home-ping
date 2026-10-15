package com.david.homeenv.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View
import com.david.homeenv.R

/**
 * Today's occupancy as 24 hourly bars, like the web dashboard's chart:
 * ≥60% green, 20–59% amber, 1–19% grey; hatched = past hour with no data,
 * dimmed = future hour, outlined + "now" label = current hour.
 */
class OccupancyBarsView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val d = resources.displayMetrics.density
    private val gap = 3 * d
    private val barsHeight = 120 * d
    private val axisGap = 4 * d
    private val radius = 4 * d

    private val faint = Levels.color(context, R.color.faint)
    private val muted = Levels.color(context, R.color.muted)

    private val colPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Levels.withAlpha(faint, 0.30f) }
    private val futurePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Levels.withAlpha(faint, 0.12f) }
    private val hatchPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Levels.withAlpha(faint, 0.45f)
        strokeWidth = 1.5f * d
    }
    private val nowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1 * d
        color = muted
    }
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val axisPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = muted
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 10f, resources.displayMetrics)
        textAlign = Paint.Align.CENTER
    }
    private val axisNowPaint = Paint(axisPaint).apply {
        color = Levels.color(context, R.color.text)
        isFakeBoldText = true
    }
    private val rect = RectF()

    private var hours: Map<Int, Double> = emptyMap()
    private var currentHour = -1

    /** [byHour]: hour (0–23) → occupancy %; missing hours = no data. */
    fun setData(byHour: Map<Int, Double>, currentHour: Int) {
        this.hours = byHour
        this.currentHour = currentHour
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val h = barsHeight + axisGap + axisPaint.textSize + 2 * d
        setMeasuredDimension(
            getDefaultSize(suggestedMinimumWidth, widthMeasureSpec),
            resolveSize(h.toInt(), heightMeasureSpec)
        )
    }

    override fun onDraw(canvas: Canvas) {
        val colW = (width - gap * 23) / 24f
        val baseline = barsHeight + axisGap - axisPaint.ascent()

        for (h in 0 until 24) {
            val left = h * (colW + gap)
            rect.set(left, 0f, left + colW, barsHeight)
            val value = hours[h]
            when {
                h > currentHour -> canvas.drawRoundRect(rect, radius, radius, futurePaint)
                value == null -> drawHatched(canvas, rect)
                else -> canvas.drawRoundRect(rect, radius, radius, colPaint)
            }

            if (value != null && h <= currentHour) {
                // Minimum 3% so a 1% hour is still visible (web does the same)
                val top = barsHeight * (1 - value.coerceIn(3.0, 100.0).toFloat() / 100f)
                barPaint.color = Levels.color(context, Levels.occupancy(value))
                rect.set(left, top, left + colW, barsHeight)
                canvas.drawRoundRect(rect, radius, radius, barPaint)
            }

            if (h == currentHour) {
                val inset = nowPaint.strokeWidth / 2
                rect.set(left + inset, inset, left + colW - inset, barsHeight - inset)
                canvas.drawRoundRect(rect, radius, radius, nowPaint)
            }

            // Axis: "now" under the current hour, every 3rd hour otherwise,
            // skipping labels right next to "now" so they don't collide
            val label = when {
                h == currentHour -> "now"
                h % 3 == 0 && (currentHour < 0 || kotlin.math.abs(h - currentHour) > 1) -> "%02d".format(h)
                else -> null
            }
            if (label != null) {
                canvas.drawText(label, left + colW / 2, baseline, if (h == currentHour) axisNowPaint else axisPaint)
            }
        }
    }

    private fun drawHatched(canvas: Canvas, r: RectF) {
        canvas.save()
        canvas.clipRect(r)
        val step = 6 * d
        var x = r.left - r.height()
        while (x < r.right) {
            canvas.drawLine(x, r.bottom, x + r.height(), r.top, hatchPaint)
            x += step
        }
        canvas.restore()
    }
}
