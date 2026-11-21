"use strict";

// Applies saved theme, presentation mode and UI scale before first paint, then wires the header controls.
// Shortcuts: Alt+P presentation mode, Alt+= / Alt+- zoom, Alt+0 reset zoom. URL: ?stage=1 or ?stage=0.
(function () {
  const root = document.documentElement;
  const SCALES = [0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.6];
  const DEFAULT_SCALE = SCALES.indexOf(1);

  if ((localStorage.getItem("wpc-theme") || "light") === "light") root.dataset.theme = "light";
  const stageParam = new URLSearchParams(location.search).get("stage");
  if (stageParam !== null) localStorage.setItem("wpc-stage", stageParam === "0" ? "off" : "on");
  if (localStorage.getItem("wpc-stage") === "on") root.dataset.stage = "";
  const savedScale = localStorage.getItem("wpc-scale");
  let scaleIndex = savedScale === null ? DEFAULT_SCALE : Number(savedScale);
  if (!Number.isInteger(scaleIndex) || scaleIndex < 0 || scaleIndex >= SCALES.length) scaleIndex = DEFAULT_SCALE;
  const applyScale = () => root.style.setProperty("--ui-scale", SCALES[scaleIndex]);
  applyScale();

  document.addEventListener("DOMContentLoaded", () => {
    const themeButton = document.getElementById("theme-toggle");
    const stageButton = document.getElementById("stage-toggle");
    const scaleLabel = document.getElementById("scale-label");

    const syncTheme = () => {
      if (!themeButton) return;
      const light = root.dataset.theme === "light";
      themeButton.textContent = light ? "☾" : "☀";
      themeButton.title = light ? "Tema scuro" : "Tema chiaro";
      themeButton.setAttribute("aria-label", themeButton.title);
      document.querySelector('meta[name="theme-color"]')?.setAttribute("content", light ? "#f4f6fd" : "#070b1a");
    };
    const syncStage = () => {
      if (!stageButton) return;
      const on = "stage" in root.dataset;
      stageButton.setAttribute("aria-pressed", String(on));
      stageButton.title = on ? "Esci dalla modalità presentazione (Alt+P)" : "Modalità presentazione per proiettore (Alt+P)";
    };
    const syncScale = () => {
      applyScale();
      localStorage.setItem("wpc-scale", String(scaleIndex));
      if (scaleLabel) scaleLabel.textContent = `${Math.round(SCALES[scaleIndex] * 100)}%`;
    };
    const toggleStage = () => {
      if ("stage" in root.dataset) delete root.dataset.stage;
      else root.dataset.stage = "";
      localStorage.setItem("wpc-stage", "stage" in root.dataset ? "on" : "off");
      syncStage();
    };
    const zoom = (step) => {
      scaleIndex = step === 0 ? DEFAULT_SCALE : Math.min(SCALES.length - 1, Math.max(0, scaleIndex + step));
      syncScale();
    };

    themeButton?.addEventListener("click", () => {
      if (root.dataset.theme === "light") delete root.dataset.theme;
      else root.dataset.theme = "light";
      localStorage.setItem("wpc-theme", root.dataset.theme || "dark");
      syncTheme();
    });
    stageButton?.addEventListener("click", toggleStage);
    document.getElementById("zoom-out")?.addEventListener("click", () => zoom(-1));
    document.getElementById("zoom-in")?.addEventListener("click", () => zoom(1));
    scaleLabel?.addEventListener("click", () => zoom(0));
    document.addEventListener("keydown", (event) => {
      if (!event.altKey || event.ctrlKey || event.metaKey) return;
      const actions = { KeyP: toggleStage, Equal: () => zoom(1), NumpadAdd: () => zoom(1), Minus: () => zoom(-1), NumpadSubtract: () => zoom(-1), Digit0: () => zoom(0), Numpad0: () => zoom(0) };
      const action = actions[event.code];
      if (action) { event.preventDefault(); action(); }
    });
    syncTheme();
    syncStage();
    syncScale();
  });
})();