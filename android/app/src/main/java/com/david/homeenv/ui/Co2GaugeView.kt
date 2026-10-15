package com.david.homeenv.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View
import com.david.homeenv.R

/**
 * CO₂ gauge from the web dashboard: [Levels.CO2] (400–3000 ppm, stops at 800/1200/2000)
 * drawn by [GaugeBar], with the scale labels underneath.
 */
class Co2GaugeView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val d = resources.displayMetrics.density
    private val gauge = GaugeBar(context)
    private val labelGap = 4 * d
    private val scale = Levels.CO2
    // Label at each band edge; the last one says "3000+"
    private val labels = (listOf(scale.min) + scale.stops + scale.max).mapIndexed { i, v ->
        scale.fraction(v).toFloat() to (if (i == scale.stops.size + 1) "${v.toInt()}+" else "${v.toInt()}")
    }

    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Levels.color(context, R.color.muted)
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 10f, resources.displayMetrics)
    }

    /** Current reading in ppm, or null to hide the needle. */
    var ppm: Int? = null
        set(value) {
            field = value
            invalidate()
        }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val h = gauge.height + labelGap + labelPaint.textSize + 2 * d
        setMeasuredDimension(
            getDefaultSize(suggestedMinimumWidth, widthMeasureSpec),
            resolveSize(h.toInt(), heightMeasureSpec)
        )
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        gauge.draw(canvas, scale, ppm?.toDouble(), w)

        // Scale: first label left-aligned, last right-aligned, others centred
        val baseline = gauge.height + labelGap - labelPaint.ascent()
        labels.forEachIndexed { i, (pos, text) ->
            val tw = labelPaint.measureText(text)
            val x = when (i) {
                0 -> 0f
                labels.lastIndex -> w - tw
                else -> pos * w - tw / 2
            }
            canvas.drawText(text, x, baseline, labelPaint)
        }
    }
}
