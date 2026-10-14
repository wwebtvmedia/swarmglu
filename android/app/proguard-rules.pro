# SwarmGlu — règles ProGuard/R8.
# Le code Python embarqué n'est pas concerné par la minification ;
# minifyEnabled est désactivé par défaut pour le PoC école.

# Serveur HTTP embarqué et crypto PQC (utilisés par le noyau natif) :
-keep class fi.iki.elonen.** { *; }
-keep class org.bouncycastle.pqc.crypto.mlkem.** { *; }
-dontwarn org.bouncycastle.**
-dontwarn javax.naming.**

# androidx.tracing.Trace : référencé par AndroidJUnitRunner au démarrage de
# l'instrumentation (process de l'app) — sans ce keep, R8 le strippe et le
# test crash en ClassNotFoundException avant même le lancement du nœud.
-keep class androidx.tracing.** { *; }

# Stdlib Kotlin + coroutines : l'APK androidTest ne les embarque PAS (il
# s'attend à les trouver dans l'app — cf. releaseAndroidTestRuntimeClasspath).
# R8 les fusionne/supprime (kotlin.LazyKt -> R8$$REMOVED$$CLASS$$, coroutines
# entièrement retirées) : l'instrumentation crash alors en NoClassDefFoundError
# (TestDirCalculator -> kotlin.LazyKt). Indispensable pour smoker l'app
# minifiée ; sans effet sur le comportement de l'app elle-même.
-keep class kotlin.** { *; }
-keep class kotlinx.coroutines.** { *; }
-dontwarn kotlinx.coroutines.**

# Pont test→app : NodeDriverInstrumentation / SwarmGluNodeInstrumentedTest
# appellent CoreBridge (object Kotlin : INSTANCE, nodeStart, ask, status…).
# Le test APK reçoit le mapping des CLASSES mais pas le renommage des
# MEMBRES → NoSuchFieldError: INSTANCE sans ce keep.
-keep class com.tree4five.swarmglu.CoreBridge { *; }
