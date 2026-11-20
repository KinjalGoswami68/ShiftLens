// Privacy page: shared styles and the theme toggle only (no motion, no API calls).
import "./styles.css";
import "./privacy.css";
import { mountThemeControls } from "./theme";

mountThemeControls(document.getElementById("theme-controls")!);
