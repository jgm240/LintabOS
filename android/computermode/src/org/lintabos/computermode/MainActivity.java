// SPDX-License-Identifier: MIT
package org.lintabos.computermode;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.TextView;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * "Computer Mode": tells LintabOS (the computer side of this tablet) to leave Android mode. It sends one request to a tiny
 * listener the LintabOS Android session runs on the Waydroid network bridge; the session then ends and the tablet returns
 * to the login screen. Tap the text to try again if it could not reach the computer side.
 */
public class MainActivity extends Activity {
    private static final String EXIT_URL = "@EXIT_URL@";
    private TextView text;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        text = new TextView(this);
        text.setTextSize(26);
        text.setGravity(Gravity.CENTER);
        text.setPadding(48, 48, 48, 48);
        text.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                leave();
            }
        });
        setContentView(text);
        leave();
    }

    private void leave() {
        say("Switching to computer mode…");
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    HttpURLConnection connection = (HttpURLConnection) new URL(EXIT_URL).openConnection();
                    connection.setConnectTimeout(4000);
                    connection.setReadTimeout(4000);
                    int code = connection.getResponseCode();
                    connection.disconnect();
                    if (code != 200) {
                        say("The computer side answered " + code + ". Tap to try again.");
                    }
                } catch (Exception e) {
                    say("Could not reach the computer side (" + e.getMessage() + "). Tap to try again.");
                }
            }
        }).start();
    }

    private void say(final String message) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                text.setText(message);
            }
        });
    }
}
