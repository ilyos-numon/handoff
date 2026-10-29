import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { watchTheme } from "./lib/theme";
import "./styles/global.css";

watchTheme();
createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
