package com.david.homeenv

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Sends the FCM token to the backend (POST /api/register_token), which subscribes it to topic "env". */
object PushRegistration {

    private const val TAG = "PushRegistration"
    private const val PREFS = "push"
    private const val KEY_TOKEN = "registered_token"
    private const val KEY_AT = "registered_at"
    // Re-send an unchanged token now and then, in case the server or Firebase forgot it
    private val RESEND_AFTER_MS = TimeUnit.DAYS.toMillis(7)

    /**
     * Blocking network call: run it off the main thread. Skips a token that was already
     * registered in the last 7 days; the app start, a recreated activity and onNewToken
     * used to send the same token up to three times.
     */
    @Synchronized
    fun register(context: Context, token: String) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val fresh = System.currentTimeMillis() - prefs.getLong(KEY_AT, 0) < RESEND_AFTER_MS
        if (prefs.getString(KEY_TOKEN, null) == token && fresh) {
            Log.i(TAG, "Token already registered")
            return
        }
        when (val code = Api.post("/api/register_token", JSONObject().put("token", token))) {
            null -> Log.w(TAG, "Failed to register token: no response")
            in 200..299 -> {
                Log.i(TAG, "Token registered")
                prefs.edit().putString(KEY_TOKEN, token).putLong(KEY_AT, System.currentTimeMillis()).apply()
            }
            else -> Log.w(TAG, "Failed to register token: HTTP $code")
        }
    }
}
