#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Builds the "Computer Mode" Android app (android/computermode) into payload/usr/share/lintabos/android/ComputerMode.apk.
# The finished APK is committed, so normal package and ISO builds don't need any of this. Run it only when the app changes.
#
# Needs Docker and network. Debian 13 has aapt, apksigner and zipalign but not the Java-to-Android compiler or the Android
# API jar, so those two (build-time only, never shipped) come from Maven and are pinned by checksum below.
set -euo pipefail
cd "$(dirname "$0")/.."

ANDROID_JAR_URL=https://repo1.maven.org/maven2/com/google/android/android/4.1.1.4/android-4.1.1.4.jar
R8_URL=https://dl.google.com/dl/android/maven2/com/android/tools/r8/8.5.35/r8-8.5.35.jar
ANDROID_JAR_SHA256=${ANDROID_JAR_SHA256:-84072541cbb711eff89f7277100ff854929a446dba7ceb1b195c340e0b4fd3cb}
R8_SHA256=${R8_SHA256:-4733945987ee0a840fafc34080b135259e01678412e07212b23f706334290294}

# One-time image with the build tools, so a retry doesn't repeat the (slow, emulated) package install.
IMG=lintabos-android-builder
if ! docker image inspect "$IMG" >/dev/null 2>&1; then
  docker rm -f lintab-android-prep >/dev/null 2>&1 || true
  docker run --name lintab-android-prep lintabos-builder bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq --no-install-recommends default-jdk-headless aapt apksigner zipalign android-framework-res \
        librsvg2-bin ca-certificates curl >/dev/null 2>&1'
  docker commit lintab-android-prep "$IMG" >/dev/null
  docker rm lintab-android-prep >/dev/null
fi

docker run --rm -v "$PWD:/lintab" -w /lintab \
  -e ANDROID_JAR_URL="$ANDROID_JAR_URL" -e R8_URL="$R8_URL" \
  -e ANDROID_JAR_SHA256="$ANDROID_JAR_SHA256" -e R8_SHA256="$R8_SHA256" "$IMG" bash -euo pipefail -c '
  W=$(mktemp -d); trap "rm -rf $W" EXIT
  fetch() { curl -fsSL -o "$2" "$1"; }
  fetch "$ANDROID_JAR_URL" $W/android.jar; fetch "$R8_URL" $W/r8.jar
  for pair in "android.jar $ANDROID_JAR_SHA256" "r8.jar $R8_SHA256"; do
    set -- $pair; got=$(sha256sum $W/$1 | cut -d" " -f1); echo "$1 sha256 $got"
    [ -z "${2:-}" ] || [ "$2" = "$got" ] || { echo "CHECKSUM MISMATCH for $1" >&2; exit 1; }
  done

  SRC=android/computermode; B=$W/build; mkdir -p $B/gen $B/classes $B/res
  cp -r $SRC/res/. $B/res/
  URL=$(PYTHONPATH=installer python3 -c "from lintab import android; print(android.EXIT_URL)")
  sed "s|@EXIT_URL@|$URL|" $SRC/src/org/lintabos/computermode/MainActivity.java > $B/MainActivity.java
  for s in "mdpi 48" "hdpi 72" "xhdpi 96" "xxhdpi 144" "xxxhdpi 192"; do
    set -- $s; mkdir -p $B/res/mipmap-$1; rsvg-convert -w $2 -h $2 $SRC/icon.svg -o $B/res/mipmap-$1/ic_launcher.png
  done

  FW=/usr/share/android-framework-res/framework-res.apk
  aapt package -f -m -J $B/gen -M $SRC/AndroidManifest.xml -S $B/res -I $FW -F $B/resources.ap_
  JH=$(dirname $(dirname $(readlink -f $(command -v javac))))
  javac -source 8 -target 8 -Xlint:-options -classpath $W/android.jar -d $B/classes $B/MainActivity.java $(find $B/gen -name "*.java")
  (cd $B/classes && find . -name "*.class" > ../classlist)
  java -cp $W/r8.jar com.android.tools.r8.D8 --min-api 21 --lib $W/android.jar --lib $JH --output $B \
      $(find $B/classes -name "*.class")
  cp $B/resources.ap_ $B/app.zip; (cd $B && aapt add app.zip classes.dex >/dev/null)
  zipalign -f 4 $B/app.zip $B/aligned.apk
  [ -f $SRC/debug.keystore ] || keytool -genkeypair -keystore $SRC/debug.keystore -storepass android -keypass android \
      -alias lintabos-computermode -keyalg RSA -keysize 2048 -validity 36500 -dname "CN=LintabOS Computer Mode debug key"
  apksigner sign --v4-signing-enabled false --ks $SRC/debug.keystore --ks-pass pass:android --key-pass pass:android --out payload/usr/share/lintabos/android/ComputerMode.apk $B/aligned.apk
  echo "== verify"; apksigner verify --verbose payload/usr/share/lintabos/android/ComputerMode.apk | head -3
  aapt dump badging payload/usr/share/lintabos/android/ComputerMode.apk | grep -E "^package|^application:|application-label:|uses-permission|launchable-activity|targetSdk|sdkVersion"
  ls -l payload/usr/share/lintabos/android/ComputerMode.apk
'
