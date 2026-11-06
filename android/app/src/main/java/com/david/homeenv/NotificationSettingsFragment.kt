package com.david.homeenv

import android.app.TimePickerDialog
import android.content.DialogInterface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CompoundButton
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.david.homeenv.ui.Levels
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.switchmaterial.SwitchMaterial
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * Notification settings fragment - part of the tabbed NotificationSettingsActivity (#289).
 */
class NotificationSettingsFragment : Fragment() {

    private var settings: JSONObject? = null
    private lateinit var container: LinearLayout
    private lateinit var status: TextView
    private var kindSwitches = mutableMapOf<String, SwitchMaterial>()

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        val root = LinearLayout(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            orientation = LinearLayout.VERTICAL
            setPadding(0, 0, 0, 0)
        }

        this.container = LinearLayout(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
            orientation = LinearLayout.VERTICAL
            setPadding(16, 4, 16, 32)
        }

        status = TextView(requireContext()).apply {
            text = "Loading…"
            setTextColor(Levels.color(requireContext(), R.color.muted))
            textSize = 13f
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = 12 } as ViewGroup.LayoutParams
        }
        this.container.addView(status)

        val scroll = android.widget.ScrollView(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            ).apply { weight = 1f }
            clipToPadding = false
            addView(this@NotificationSettingsFragment.container)
        }
        root.addView(scroll)
        return root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        load()
    }

    override fun onResume() {
        super.onResume()
        loadOccupancyArmedState()
        setAutoSwitchSilently(HomeLocation.autoEnabled(requireContext()))
        showAutoStatus()
    }

    private fun load() {
        lifecycleScope.launch {
            val s = withContext(Dispatchers.IO) { Api.getObject("/api/notification_settings") }
            if (s == null) {
                status.text = "Can't reach the server. Settings can't be changed right now."
            } else {
                status.text = "Changes apply right away."
                show(s)
            }
        }
    }

    private fun show(s: JSONObject) {
        settings = s
        container.removeAllViews()
        container.addView(status)

        // Alerts section - simplified for fragment context
        for (kind in listOf("humidity", "temperature", "radar", "nodes", "pressure", "air")) {
            val switch = SwitchMaterial(requireContext()).apply {
                text = kind.replaceFirstChar { it.uppercase() }
                isChecked = s.optBoolean("$kind", true)
                setOnCheckedChangeListener { _, isChecked ->
                    setSetting(kind, isChecked)
                }
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = 8 }
            }
            kindSwitches[kind] = switch
            container.addView(switch)
        }
    }

    private fun setSetting(kind: String, enabled: Boolean) {
        lifecycleScope.launch {
            val data = JSONObject().put(kind, enabled)
            val result: JSONObject? = withContext(Dispatchers.IO) {
                Api.postForObject("/api/notification_settings", data)
            }
            if (result == null) {
                kindSwitches[kind]?.isChecked = !enabled
                Snackbar.make(container, "Couldn't save setting", Snackbar.LENGTH_SHORT).show()
            }
        }
    }

    private fun loadOccupancyArmedState() {
        // Stub for fragment context
    }

    private fun setAutoSwitchSilently(enabled: Boolean) {
        // Stub for fragment context
    }

    private fun showAutoStatus() {
        // Stub for fragment context
    }
}
