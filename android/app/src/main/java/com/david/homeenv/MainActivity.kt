package com.david.homeenv

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.util.Base64
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Switch
import android.widget.TableRow
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.fragment.app.FragmentActivity
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import androidx.viewpager2.adapter.FragmentStateAdapter
import androidx.viewpager2.widget.ViewPager2
import com.google.android.material.tabs.TabLayout
import com.google.android.material.tabs.TabLayoutMediator
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale
import kotlin.concurrent.thread
import com.david.homeenv.BuildConfig
class MainActivity : AppCompatActivity() {

    private lateinit var viewPager: ViewPager2
    private lateinit var tabLayout: TabLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Check and request exact alarm permission for Android 12+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val alarmManager = getSystemService(Context.ALARM_SERVICE) as android.app.AlarmManager
            if (!alarmManager.canScheduleExactAlarms()) {
                val intent = android.content.Intent(
                    android.provider.Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM
                )
                try {
                    startActivity(intent)
                } catch (e: Exception) {
                    e.printStackTrace()
                }
            }
        }

        // Push notifications (off in the preview build, which has no Firebase config)
        if (BuildConfig.PUSH_ENABLED) setupPush()

        // Setup ViewPager and TabLayout
        viewPager = findViewById(R.id.viewPager)
        tabLayout = findViewById(R.id.tabLayout)

        val adapter = ViewPagerAdapter(this)
        viewPager.adapter = adapter

        TabLayoutMediator(tabLayout, viewPager) { tab, position ->
            tab.text = when (position) {
                0 -> "Home"
                1 -> "Plants"
                else -> "Tab $position"
            }
        }.attach()
    }

    private fun setupPush() {
        if (FirebaseApp.getApps(this).isEmpty()) {
            FirebaseApp.initializeApp(this)
        }

        // Subscribe to topic for notifications
        FirebaseMessaging.getInstance().subscribeToTopic("env")
            .addOnCompleteListener { task ->
                println("Subscribed to topic 'env': ${task.isSuccessful}")
            }

        // Request notification permission on Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            when {
                ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS
                ) == PackageManager.PERMISSION_GRANTED -> getAndRegisterToken()
                else -> requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        } else {
            getAndRegisterToken()
        }
    }

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) getAndRegisterToken()
        }

    private fun getAndRegisterToken() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return

        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { token ->
                println("FCM token: $token")
                Thread { PushRegistration.register(token) }.start()
            }
            .addOnFailureListener {
                it.printStackTrace()
            }
    }

    // ViewPager Adapter
    private inner class ViewPagerAdapter(fa: FragmentActivity) : FragmentStateAdapter(fa) {
        override fun getItemCount(): Int = 2

        override fun createFragment(position: Int): Fragment {
            return when (position) {
                0 -> HomeFragment()
                1 -> PlantsFragment()
                else -> HomeFragment()
            }
        }
    }
}

// Plants Monitor Fragment
class PlantsFragment : Fragment() {

    private val soilLatestUrl = "${BuildConfig.API_BASE_URL}/api/soil/latest"
    private val credentials = "${BuildConfig.API_USER}:${BuildConfig.API_PASS}"

    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var soilMoistureValue: TextView
    private lateinit var soilStatus: TextView
    private lateinit var soilLastUpdate: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_plants, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        // Initialize views
        swipeRefresh = view.findViewById(R.id.swipeRefreshPlants)
        soilMoistureValue = view.findViewById(R.id.soil_moisture_value)
        soilStatus = view.findViewById(R.id.soil_status)
        soilLastUpdate = view.findViewById(R.id.soil_last_update)

        // Setup swipe refresh
        swipeRefresh.setOnRefreshListener {
            loadSoilData()
        }

        // Load initial data
        loadSoilData()
    }

    private fun loadSoilData() {
        thread {
            try {
                val client = OkHttpClient()
                val authHeader = "Basic " + Base64.encodeToString(
                    credentials.toByteArray(),
                    Base64.NO_WRAP
                )

                val soilRequest = Request.Builder()
                    .url(soilLatestUrl)
                    .addHeader("Authorization", authHeader)
                    .build()

                val soilResponse = client.newCall(soilRequest).execute()
                if (soilResponse.isSuccessful) {
                    val soilBody = soilResponse.body?.string() ?: return@thread
                    val soil = JSONObject(soilBody)

                    activity?.runOnUiThread {
                        val moisture = if (soil.has("moisture")) {
                            soil.getInt("moisture")
                        } else {
                            0
                        }

                        soilMoistureValue.text = "$moisture %"

                        val (status, color) = when {
                            moisture == 0 -> Pair("No data", Color.parseColor("#94a3b8"))
                            moisture < 30 -> Pair("Needs Water", Color.parseColor("#f87171"))
                            moisture < 50 -> Pair("Low", Color.parseColor("#fbbf24"))
                            moisture < 70 -> Pair("Good", Color.parseColor("#4ade80"))
                            else -> Pair("Wet", Color.parseColor("#60a5fa"))
                        }

                        soilStatus.text = status
                        soilStatus.setTextColor(color)

                        if (soil.has("timestamp")) {
                            try {
                                val timestamp = soil.getString("timestamp")
                                val inputFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                                val outputFormat = SimpleDateFormat("MMM dd, HH:mm", Locale.US)
                                val date = inputFormat.parse(timestamp)
                                soilLastUpdate.text = "Last update: ${outputFormat.format(date!!)}"
                            } catch (e: Exception) {
                                soilLastUpdate.text = "Last update: ${soil.optString("timestamp", "--")}"
                            }
                        } else if (soil.has("ts")) {
                            try {
                                val timestamp = soil.getString("ts")
                                val inputFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                                val outputFormat = SimpleDateFormat("MMM dd, HH:mm", Locale.US)
                                val date = inputFormat.parse(timestamp)
                                soilLastUpdate.text = "Last update: ${outputFormat.format(date!!)}"
                            } catch (e: Exception) {
                                soilLastUpdate.text = "Last update: --"
                            }
                        } else {
                            soilLastUpdate.text = "Last update: --"
                        }

                        swipeRefresh.isRefreshing = false
                    }
                } else {
                    activity?.runOnUiThread {
                        soilMoistureValue.text = "-- %"
                        soilStatus.text = "No data"
                        soilLastUpdate.text = "Last update: --"
                        swipeRefresh.isRefreshing = false
                    }
                }

            } catch (e: Exception) {
                e.printStackTrace()
                activity?.runOnUiThread {
                    soilMoistureValue.text = "Error"
                    soilStatus.text = "Error"
                    swipeRefresh.isRefreshing = false
                }
            }
        }
    }
}
