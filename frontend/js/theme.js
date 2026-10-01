const THEME_KEY = "belajar-ai-theme";

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
}

function loadTheme() {
    const savedTheme = localStorage.getItem(THEME_KEY);

    if (savedTheme === "dark" || savedTheme === "light") {
        applyTheme(savedTheme);
    } else {
        applyTheme("light");
    }
}

function toggleTheme() {
    const currentTheme =
        document.documentElement.getAttribute("data-theme") || "light";

    const newTheme = currentTheme === "dark" ? "light" : "dark";

    applyTheme(newTheme);
    localStorage.setItem(THEME_KEY, newTheme);
}

document.addEventListener("DOMContentLoaded", () => {
    loadTheme();

    const themeButton = document.getElementById("theme-btn");

    if (themeButton) {
        themeButton.addEventListener("click", toggleTheme);
    }
});