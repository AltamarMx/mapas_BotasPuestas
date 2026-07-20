(() => {
  const routeFromUrl = () => new URL(window.location.href).searchParams.get("ruta");

  const applyRouteFromUrl = () => {
    const route = routeFromUrl();
    const select = document.getElementById("ruta");
    if (!route || !select || !Array.from(select.options).some((item) => item.value === route)) {
      return;
    }
    select.value = route;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  };

  document.addEventListener("shiny:connected", applyRouteFromUrl, { once: true });
  window.addEventListener("popstate", applyRouteFromUrl);
  document.addEventListener("change", (event) => {
    if (!(event.target instanceof HTMLSelectElement) || event.target.id !== "ruta") {
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("ruta", event.target.value);
    window.history.replaceState({}, "", url);
  });
})();

