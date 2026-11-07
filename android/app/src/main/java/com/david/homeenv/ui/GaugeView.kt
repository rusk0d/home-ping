package com.david.homeenv.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View
import com.david.homeenv.R

/**
 * A gauge from the web dashboard: a [Scale] drawn by [GaugeBar], with the scale labels
 * underneath. The CO₂ card ([Levels.CO2], the default) and the fridge card ([Levels.FRIDGE],
 * #326). Labels that would overlap a neighbour on a narrow card are left out; the first and
 * the last one always stay.
 */
class GaugeView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val d = resources.displayMetrics.density
    private val gauge = GaugeBar(context)
    private val labelGap = 4 * d
    private val minLabelSpace = 6 * d

    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Levels.color(context, R.color.muted)
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 10f, resources.displayMetrics)
    }

    var scale: Scale = Levels.CO2
        set(value) {
            field = value
            invalidate()
        }

    /** The current reading on [scale]'s units, or null to hide the needle. */
    var value: Double? = null
        set(v) {
            field = v
            invalidate()
        }

    /** A label at each band edge; the last one says "3000+" (the scale ends there, readings don't). */
    private fun labels(): List<Pair<Float, String>> {
        val values = listOf(scale.min) + scale.stops + scale.max
        return values.mapIndexed { i, v ->
            scale.fraction(v).toFloat() to (if (i == values.lastIndex) "${v.toInt()}+" else "${v.toInt()}")
        }
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
        gauge.draw(canvas, scale, value, w)

        // Scale: first label left-aligned, last right-aligned, others centred
        val labels = labels()
        val placed = labels.mapIndexed { i, (pos, text) ->
            val tw = labelPaint.measureText(text)
            val x = when (i) {
                0 -> 0f
                labels.lastIndex -> w - tw
                else -> pos * w - tw / 2
            }
            Triple(x, x + tw, text)
        }
        // Keep a middle label only if it clears the last kept one and the last label
        val last = placed.last()
        var right = Float.NEGATIVE_INFINITY
        placed.forEachIndexed { i, (start, end, text) ->
            val keep = i == 0 || i == placed.lastIndex ||
                (start >= right + minLabelSpace && end + minLabelSpace <= last.first)
            if (!keep) return@forEachIndexed
            canvas.drawText(text, start, gauge.height + labelGap - labelPaint.ascent(), labelPaint)
            right = end
        }
    }
}
