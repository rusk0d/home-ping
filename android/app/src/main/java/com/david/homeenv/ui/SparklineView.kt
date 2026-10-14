package com.david.homeenv.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.View
import com.david.homeenv.R

/**
 * Small line chart with a soft fill under it (web: `.spark`). Points are drawn
 * left to right, oldest first, scaled to their own min/max.
 */
class SparklineView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val d = resources.displayMetrics.density
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2 * d
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
    }
    private val areaPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val line = Path()
    private val area = Path()
    private var points: List<Double> = emptyList()

    var color: Int = Color.GRAY
        set(value) {
            field = value
            linePaint.color = value
            areaPaint.color = Levels.withAlpha(value, 0.16f)
            invalidate()
        }

    // After `color` is declared, so its initializer doesn't overwrite this
    init {
        context.obtainStyledAttributes(attrs, R.styleable.AccentCard).apply {
            color = getColor(R.styleable.AccentCard_accentColor, Levels.color(context, R.color.muted))
            recycle()
        }
    }

    fun setPoints(values: List<Double>) {
        points = values
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(
            getDefaultSize(suggestedMinimumWidth, widthMeasureSpec),
            resolveSize((40 * d).toInt(), heightMeasureSpec)
        )
    }

    override fun onDraw(canvas: Canvas) {
        if (points.size < 2) return
        var min = points.min()
        var max = points.max()
        if (max - min < 1e-9) { min -= 1; max += 1 }

        // Keep the stroke inside the view
        val pad = linePaint.strokeWidth
        val w = width - 2 * pad
        val h = height - 2 * pad
        fun x(i: Int) = pad + w * i / (points.size - 1)
        fun y(v: Double) = pad + h * (1 - ((v - min) / (max - min))).toFloat()

        line.reset()
        points.forEachIndexed { i, v -> if (i == 0) line.moveTo(x(i), y(v)) else line.lineTo(x(i), y(v)) }
        area.reset()
        area.addPath(line)
        area.lineTo(x(points.lastIndex), height.toFloat())
        area.lineTo(x(0), height.toFloat())
        area.close()

        canvas.drawPath(area, areaPaint)
        canvas.drawPath(line, linePaint)
    }
}
