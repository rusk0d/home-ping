package com.david.homeenv

import android.Manifest
import android.annotation.SuppressLint
import android.app.TimePickerDialog
import android.content.DialogInterface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CompoundButton
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.david.homeenv.databinding.FragmentNotificationSettingsBinding
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

class NotificationSettingsFragment : Fragment() {

    private lateinit var b: FragmentNotificationSettingsBinding
    private var settings: JSONObject? = null

    private val kindSwitches by lazy {
        mapOf(
            "humidity" to b.humidity,
            "temperature" to b.temperature,
            "radar" to b.radar,
            "nodes" to b.nodes,
            "pressure" to b.pressure,
            "air" to b.air,
            "baseline" to b.baseline,
            "fridge" to b.fridge
        )
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        b = FragmentNotificationSettingsBinding.inflate(inflater, container, false)
        return b.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        b.quietStartRow.setOnClickListener { pickTime("start", b.quietStart) }
        b.quietEndRow.setOnClickListener { pickTime("end", b.quietEnd) }
        b.occupancySwitch.setOnCheckedChangeListener { _, isChecked -> setOccupancyArmed(isChecked) }
        b.autoLocationSwitch.setOnCheckedChangeListener { _, isChecked -> onAutoSwitchChanged(isChecked) }
        b.setHomeButton.setOnClickListener { onSetHomeClicked() }
        b.resumeAutoButton.setOnClickListener { onResumeAutoClicked() }
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
                b.status.text = "Can't reach the server. Settings can't be changed right now."
            } else {
                b.status.text = "Changes apply right away."
                show(s)
            }
        }
    }

    private fun show(s: JSONObject) {
        settings = s
        kindSwitches.forEach { (kind, sw) ->
            setSilently(sw, s.optBoolean(kind, true)) { checked -> save(JSONObject().put(kind, checked)) }
        }
        val quiet = s.optJSONObject("quiet_hours") ?: JSONObject()
        setSilently(b.quietEnabled, quiet.optBoolean("enabled")) { checked ->
            save(JSONObject().put("quiet_hours", JSONObject().put("enabled", checked)))
        }
        b.quietStart.text = quiet.optString("start", "22:00")
        b.quietEnd.text = quiet.optString("end", "07:00")
        b.quietStartRow.isEnabled = true
        b.quietEndRow.isEnabled = true
    }

    private fun setSilently(sw: SwitchMaterial, checked: Boolean, onChange: (Boolean) -> Unit) {
        sw.setOnCheckedChangeListener(null)
        sw.isChecked = checked
        sw.isEnabled = true
        sw.setOnCheckedChangeListener { _: CompoundButton, isChecked -> onChange(isChecked) }
    }

    private fun pickTime(field: String, label: TextView) {
        if (settings == null) return
        val (h, m) = label.text.split(":").map { it.toIntOrNull() ?: 0 }.let { it[0] to it.getOrElse(1) { 0 } }
        val dialog = TimePickerDialog(requireContext(), { _, hour, minute ->
            val hhmm = "%02d:%02d".format(hour, minute)
            label.text = hhmm
            save(JSONObject().put("quiet_hours", JSONObject().put(field, hhmm)))
        }, h, m, true)
        dialog.show()
        val accent = Levels.color(requireContext(), R.color.good)
        dialog.getButton(DialogInterface.BUTTON_POSITIVE)?.setTextColor(accent)
        dialog.getButton(DialogInterface.BUTTON_NEGATIVE)?.setTextColor(accent)
    }

    private fun save(change: JSONObject) {
        lifecycleScope.launch {
            val updated = withContext(Dispatchers.IO) { Api.postForObject("/api/notification_settings", change) }
            if (updated != null) {
                show(updated)
            } else {
                settings?.let { show(it) }
                Snackbar.make(b.root, "Couldn't save. Check the connection.", Snackbar.LENGTH_LONG).show()
            }
        }
    }

    // ---------- occupancy arrival alert and location auto-arm ----------

    private fun setOccupancyArmed(armed: Boolean) {
        lifecycleScope.launch {
            val ok = withContext(Dispatchers.IO) {
                Api.post("/api/occupancy_arm", JSONObject().put("armed", armed).put("source", "manual")) in 200..299
            }
            if (ok) {
                HomeLocation.setManualOverride(requireContext(), true)
                ArmWorker.cancel(requireContext())
                showArmed(armed)
                showAutoStatus()
            } else {
                setSwitchSilently(!armed)
                Snackbar.make(b.root, "Couldn't change occupancy alerts. Check the connection.", Snackbar.LENGTH_LONG).show()
            }
        }
    }

    private fun loadOccupancyArmedState() {
        lifecycleScope.launch {
            val armed = withContext(Dispatchers.IO) {
                Api.getObject("/api/occupancy_status")?.takeIf { it.has("armed") }?.optBoolean("armed")
            }
            if (armed == null) {
                b.occupancyStatus.text = "Unknown (offline?)"
            } else {
                setSwitchSilently(armed)
                showArmed(armed)
            }
        }
    }

    private fun setSwitchSilently(checked: Boolean) {
        b.occupancySwitch.setOnCheckedChangeListener(null)
        b.occupancySwitch.isChecked = checked
        b.occupancySwitch.setOnCheckedChangeListener { _, isChecked -> setOccupancyArmed(isChecked) }
    }

    private var lastArmed = false

    private fun showArmed(armed: Boolean) {
        lastArmed = armed
        val mode = when {
            !HomeLocation.autoEnabled(requireContext()) -> ""
            HomeLocation.manualOverride(requireContext()) -> " (manual)"
            else -> " (auto · location)"
        }
        b.occupancyStatus.text = (if (armed) "Armed – push on arrival" else "Off") + mode
        b.resumeAutoButton.visibility =
            if (HomeLocation.autoEnabled(requireContext()) && HomeLocation.manualOverride(requireContext())) View.VISIBLE else View.GONE
    }

    private fun onResumeAutoClicked() {
        val ctx = requireContext()
        HomeLocation.setManualOverride(ctx, false)
        HomeGeofence.syncToCurrentLocation(ctx)
        showArmed(lastArmed)
        b.root.postDelayed({ if (isResumed) loadOccupancyArmedState() }, 2_000)
    }

    // ---------- location auto-arm ----------

    private var pendingHome = false

    private val fineLocationRequest = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) {
            pendingHome = false
            setAutoSwitchSilently(false)
            showAutoStatus("Location permission was refused")
        } else if (pendingHome) {
            pendingHome = false
            setHomeHere()
        } else {
            continueAutoOn()
        }
    }

    private val backgroundLocationRequest = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            continueAutoOn()
        } else {
            setAutoSwitchSilently(false)
            showAutoStatus("Allow location \"All the time\" for the auto-arm")
        }
    }

    private fun onAutoSwitchChanged(on: Boolean) {
        val ctx = requireContext()
        if (!on) {
            HomeLocation.setAutoEnabled(ctx, false)
            HomeGeofence.unregister(ctx)
            showAutoStatus()
            return
        }
        if (HomeLocation.get(ctx) == null) {
            setAutoSwitchSilently(false)
            showAutoStatus("Set this place as home first")
            return
        }
        continueAutoOn()
    }

    private fun continueAutoOn() {
        val ctx = requireContext()
        if (!HomeGeofence.granted(ctx, Manifest.permission.ACCESS_FINE_LOCATION)) {
            fineLocationRequest.launch(Manifest.permission.ACCESS_FINE_LOCATION)
            return
        }
        if (!HomeGeofence.hasLocationPermissions(ctx)) {
            backgroundLocationRequest.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            return
        }
        HomeLocation.setAutoEnabled(ctx, true)
        HomeLocation.setManualOverride(ctx, false)
        HomeGeofence.register(ctx)
        HomeGeofence.syncToCurrentLocation(ctx)
        showAutoStatus()
    }

    private fun onSetHomeClicked() {
        val ctx = requireContext()
        if (!HomeGeofence.granted(ctx, Manifest.permission.ACCESS_FINE_LOCATION)) {
            pendingHome = true
            fineLocationRequest.launch(Manifest.permission.ACCESS_FINE_LOCATION)
        } else {
            setHomeHere()
        }
    }

    @SuppressLint("MissingPermission")
    private fun setHomeHere() {
        val ctx = requireContext()
        LocationServices.getFusedLocationProviderClient(ctx)
            .getCurrentLocation(Priority.PRIORITY_HIGH_ACCURACY, CancellationTokenSource().token)
            .addOnSuccessListener { here ->
                if (here == null) {
                    showAutoStatus("Couldn't get the location, try again outside")
                    return@addOnSuccessListener
                }
                HomeLocation.set(ctx, here.latitude, here.longitude)
                if (HomeLocation.autoEnabled(ctx)) {
                    HomeGeofence.register(ctx)
                    HomeGeofence.syncToCurrentLocation(ctx)
                }
                showAutoStatus("Home point saved")
            }
            .addOnFailureListener { showAutoStatus("Couldn't get the location, try again") }
    }

    private fun setAutoSwitchSilently(on: Boolean) {
        b.autoLocationSwitch.setOnCheckedChangeListener(null)
        b.autoLocationSwitch.isChecked = on
        b.autoLocationSwitch.setOnCheckedChangeListener { _, isChecked -> onAutoSwitchChanged(isChecked) }
    }

    private fun showAutoStatus(note: String? = null) {
        if (!isResumed) return
        val ctx = requireContext()
        val state = when {
            HomeLocation.get(ctx) == null -> "No home point set"
            HomeLocation.autoEnabled(ctx) -> "Home point set · auto on"
            else -> "Home point set · auto off"
        }
        b.autoLocationStatus.text = if (note == null) state else "$state · $note"
        b.setHomeButton.text = if (HomeLocation.get(ctx) == null) "Set this place as home" else "Update home point"
    }
}
