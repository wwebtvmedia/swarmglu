# SwarmGlu — règles ProGuard/R8.
# Le code Python embarqué n'est pas concerné par la minification ;
# minifyEnabled est désactivé par défaut pour le PoC école.

# Serveur HTTP embarqué et crypto PQC (utilisés par le noyau natif) :
-keep class fi.iki.elonen.** { *; }
-keep class org.bouncycastle.pqc.crypto.mlkem.** { *; }
-dontwarn org.bouncycastle.**
-dontwarn javax.naming.**
