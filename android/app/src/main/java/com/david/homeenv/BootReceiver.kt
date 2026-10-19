package com.david.homeenv

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Geofences are dropped on reboot (#239): add the home one again. */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) HomeGeofence.register(context)
    }
}
