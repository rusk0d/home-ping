package com.david.homeenv

import android.Manifest
import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.os.Build
import android.util.Log
import androidx.core.content.ContextCompat
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingRequest
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import org.json.JSONObject

/**
 * The home geofence for the location auto-arm (#239). Play services fires [GeofenceReceiver]
 * on each crossing; this object only adds, removes and checks it.
 */
object HomeGeofence {
    private const val TAG = "HomeGeofence"
    private const val REQUEST_ID = "home"

    fun hasLocationPermissions(ctx: Context): Boolean =
        granted(ctx, Manifest.permission.ACCESS_FINE_LOCATION) &&
            (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q || granted(ctx, Manifest.permission.ACCESS_BACKGROUND_LOCATION))

    fun granted(ctx: Context, permission: String) =
        ContextCompat.checkSelfPermission(ctx, permission) == PackageManager.PERMISSION_GRANTED

    private fun pendingIntent(ctx: Context): PendingIntent = PendingIntent.getBroadcast(
        ctx, 0, Intent(ctx, GeofenceReceiver::class.java),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
    )

    /**
     * Adds the geofence when auto-arm is on and a home point is set. Adding with the same
     * request ID replaces the old one, so this is safe to call again (boot, after changes).
     */
    @SuppressLint("MissingPermission")   // checked by hasLocationPermissions() first
    fun register(ctx: Context) {
        val (lat, lng) = HomeLocation.get(ctx) ?: return
        if (!HomeLocation.autoEnabled(ctx) || !hasLocationPermissions(ctx)) return
        val geofence = Geofence.Builder()
            .setRequestId(REQUEST_ID)
            .setCircularRegion(lat, lng, HomeLocation.RADIUS_M)
            .setExpirationDuration(Geofence.NEVER_EXPIRE)
            .setTransitionTypes(Geofence.GEOFENCE_TRANSITION_ENTER or Geofence.GEOFENCE_TRANSITION_EXIT)
            .build()
        val request = GeofencingRequest.Builder()
            .setInitialTrigger(0)   // no event for the current state; see syncToCurrentLocation()
            .addGeofence(geofence)
            .build()
        LocationServices.getGeofencingClient(ctx).addGeofences(request, pendingIntent(ctx))
            .addOnFailureListener { Log.w(TAG, "geofence not added: $it") }
    }

    fun unregister(ctx: Context) {
        LocationServices.getGeofencingClient(ctx).removeGeofences(pendingIntent(ctx))
    }

    /**
     * Turning auto-arm on while away must arm at once: the geofence only reports crossings.
     * Looks up where the phone is now and posts the matching state (source "location").
     */
    @SuppressLint("MissingPermission")   // checked by hasLocationPermissions() first
    fun syncToCurrentLocation(ctx: Context) {
        val (lat, lng) = HomeLocation.get(ctx) ?: return
        if (!hasLocationPermissions(ctx)) return
        LocationServices.getFusedLocationProviderClient(ctx)
            .getCurrentLocation(Priority.PRIORITY_BALANCED_POWER_ACCURACY, CancellationTokenSource().token)
            .addOnSuccessListener { here: Location? ->
                if (here == null) return@addOnSuccessListener
                val metres = FloatArray(1)
                Location.distanceBetween(here.latitude, here.longitude, lat, lng, metres)
                val away = metres[0] > HomeLocation.RADIUS_M
                Thread { postArm(away) }.start()
            }
    }

    /** Blocking: call it off the main thread. Returns the HTTP status, or null. */
    fun postArm(armed: Boolean): Int? =
        Api.post("/api/occupancy_arm", JSONObject().put("armed", armed).put("source", "location"))
}
