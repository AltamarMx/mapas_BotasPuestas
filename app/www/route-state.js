(() => {
  let eventNumber = 0;

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const trigger = event.target.closest("[data-builder-action]");
    if (!(trigger instanceof HTMLButtonElement) || trigger.disabled || !window.Shiny) {
      return;
    }
    eventNumber += 1;
    window.Shiny.setInputValue(
      "builder_action",
      {
        action: trigger.dataset.builderAction,
        segment_id: trigger.dataset.segmentId || null,
        event_number: eventNumber,
      },
      { priority: "event" },
    );
  });
})();
