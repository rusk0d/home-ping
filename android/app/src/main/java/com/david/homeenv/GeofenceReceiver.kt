package com.david.homeenv

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingEvent

/**
 * The home geofence was crossed (#239): leaving home arms the occupancy alert, coming back
 * disarms it. The transition also ends a manual override (#240): the next crossing decides.
 */
class GeofenceReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val event = GeofencingEvent.fromIntent(intent) ?: return
        if (event.hasError()) {
            Log.w(TAG, "geofence error ${event.errorCode}")
            return
        }
        val armed = when (event.geofenceTransition) {
            Geofence.GEOFENCE_TRANSITION_EXIT -> true
            Geofence.GEOFENCE_TRANSITION_ENTER -> false
            else -> return
        }
        HomeLocation.setManualOverride(context, false)
        // The post is blocking; keep the receiver alive until it's done
        val pending = goAsync()
        Thread {
            try {
                val status = HomeGeofence.postArm(armed)
                if (status == null || status !in 200..299) Log.w(TAG, "arm not sent (status $status)")
            } finally {
                pending.finish()
            }
        }.start()
    }

    companion object {
        private const val TAG = "GeofenceReceiver"
    }
}
