package com.david.homeenv.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import com.david.homeenv.R

/**
 * Draws a [Scale] as the dashboard's gauge: a pill-shaped bar in the band colours and a
 * needle at the current value, with a thin card-coloured outline so it stands out.
 * Used by [Co2GaugeView] (the CO₂ card) and [WidgetGauge] (bitmaps for the widget).
 * The gauge is [heightDp] tall (default [HEIGHT_DP]); the bar, half as tall, sits in the middle.
 */
class GaugeBar(private val context: Context, heightDp: Float = HEIGHT_DP) {

    companion object {
        const val HEIGHT_DP = 16f
    }

    private val d = context.resources.displayMetrics.density
    private val barHeight = heightDp / 2 * d
    private val needleWidth = 4 * d
    private val needleHeight = heightDp * d
    private val edge = 2 * d

    private val bandPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val needlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Levels.color(context, R.color.text) }
    private val outlinePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Levels.color(context, R.color.card) }
    private val clip = Path()
    private val rect = RectF()

    /** Height in pixels. */
    val height: Float get() = needleHeight

    /**
     * Draws [scale] [width] px wide at the top of [canvas], with the needle at [value]
     * (null = no needle). [dimWhenEmpty]: fade the bands when there is no value.
     */
    fun draw(canvas: Canvas, scale: Scale, value: Double?, width: Float, dimWhenEmpty: Boolean = false) {
        val barTop = (needleHeight - barHeight) / 2

        // Bands, clipped to a pill shape
        rect.set(0f, barTop, width, barTop + barHeight)
        clip.reset()
        clip.addRoundRect(rect, barHeight / 2, barHeight / 2, Path.Direction.CW)
        canvas.save()
        canvas.clipPath(clip)
        val starts = scale.bandStarts
        val alpha = if (value == null && dimWhenEmpty) 0.3f else 0.9f
        scale.colors.forEachIndexed { i, colorRes ->
            val end = starts.getOrNull(i + 1) ?: 1f
            bandPaint.color = Levels.withAlpha(Levels.color(context, colorRes), alpha)
            canvas.drawRect(starts[i] * width, barTop, end * width, barTop + barHeight, bandPaint)
        }
        canvas.restore()

        // Needle
        value ?: return
        val x = scale.fraction(value).toFloat() * (width - needleWidth) + needleWidth / 2
        rect.set(x - needleWidth / 2 - edge, 0f, x + needleWidth / 2 + edge, needleHeight)
        canvas.drawRoundRect(rect, 3 * d, 3 * d, outlinePaint)
        rect.set(x - needleWidth / 2, edge, x + needleWidth / 2, needleHeight - edge)
        canvas.drawRoundRect(rect, 2 * d, 2 * d, needlePaint)
    }
}
