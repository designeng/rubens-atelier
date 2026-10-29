import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";
import { applyTheme, loadTheme } from "./theme";

// Before mounting, so a stored theme is in place for the first paint.
applyTheme(loadTheme());
createApp(App).mount("#app");
