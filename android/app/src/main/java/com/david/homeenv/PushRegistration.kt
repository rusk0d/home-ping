package com.david.homeenv

import android.content.Context
import android.util.Base64
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Sends the FCM token to the backend (POST /api/register_token), which subscribes it to topic "env". */
object PushRegistration {

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
            println("Token already registered")
            return
        }
        try {
            val client = OkHttpClient()
            val mediaType = "application/json; charset=utf-8".toMediaType()
            val json = JSONObject().apply { put("token", token) }
            val body = json.toString().toRequestBody(mediaType)

            val authHeader = "Basic " + Base64.encodeToString(
                "${BuildConfig.API_USER}:${BuildConfig.API_PASS}".toByteArray(),
                Base64.NO_WRAP
            )

            val request = Request.Builder()
                .url("${BuildConfig.API_BASE_URL}/api/register_token")
                .post(body)
                .addHeader("Authorization", authHeader)
                .build()

            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    println("Failed to register token: ${response.code}")
                } else {
                    println("Token registered successfully")
                    prefs.edit().putString(KEY_TOKEN, token).putLong(KEY_AT, System.currentTimeMillis()).apply()
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }
}
