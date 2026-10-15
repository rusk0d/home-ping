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

/**
 * CO₂ gauge from the web dashboard: a 400–3000 ppm bar in four level colours
 * (stops at 800/1200/2000 ppm = 15.4% / 30.8% / 61.5%, matching [Levels.co2]),
 * a needle at the current value and the scale labels underneath.
 */
class Co2GaugeView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val d = resources.displayMetrics.density
    private val barHeight = 8 * d
    private val needleWidth = 4 * d
    private val needleHeight = 16 * d
    private val labelGap = 4 * d

    private val segments = listOf(
        0f to R.color.good, 0.154f to R.color.ok, 0.308f to R.color.poor, 0.615f to R.color.bad
    )
    private val labels = listOf(0f to "400", 0.154f to "800", 0.308f to "1200", 0.615f to "2000", 1f to "3000+")

    private val segPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val needlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Levels.color(context, R.color.text) }
    private val needleEdge = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Levels.color(context, R.color.card) }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Levels.color(context, R.color.muted)
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 10f, resources.displayMetrics)
    }
    private val clip = Path()
    private val rect = RectF()

    /** Current reading in ppm, or null to hide the needle. */
    var ppm: Int? = null
        set(value) {
            field = value
            invalidate()
        }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val h = needleHeight + labelGap + labelPaint.textSize + 2 * d
        setMeasuredDimension(
            getDefaultSize(suggestedMinimumWidth, widthMeasureSpec),
            resolveSize(h.toInt(), heightMeasureSpec)
        )
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val barTop = (needleHeight - barHeight) / 2

        // Bar, clipped to a pill shape
        rect.set(0f, barTop, w, barTop + barHeight)
        clip.reset()
        clip.addRoundRect(rect, barHeight / 2, barHeight / 2, Path.Direction.CW)
        canvas.save()
        canvas.clipPath(clip)
        segments.forEachIndexed { i, (start, colorRes) ->
            val end = segments.getOrNull(i + 1)?.first ?: 1f
            segPaint.color = Levels.withAlpha(Levels.color(context, colorRes), 0.9f)
            canvas.drawRect(start * w, barTop, end * w, barTop + barHeight, segPaint)
        }
        canvas.restore()

        // Needle with a thin card-coloured outline so it stands out on the bar
        ppm?.let {
            val x = ((it - 400f) / 2600f).coerceIn(0f, 1f) * (w - needleWidth) + needleWidth / 2
            val edge = 2 * d
            rect.set(x - needleWidth / 2 - edge, -0f, x + needleWidth / 2 + edge, needleHeight)
            canvas.drawRoundRect(rect, 3 * d, 3 * d, needleEdge)
            rect.set(x - needleWidth / 2, edge, x + needleWidth / 2, needleHeight - edge)
            canvas.drawRoundRect(rect, 2 * d, 2 * d, needlePaint)
        }

        // Scale: first label left-aligned, last right-aligned, others centred
        val baseline = needleHeight + labelGap - labelPaint.ascent()
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
