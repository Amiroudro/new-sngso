(() => {
  const nav = document.getElementById("primary-nav");
  const views = [...document.querySelectorAll(".view")];
  const labels = {
    overview: ["Editorial overview", "A clear view of what is moving, what needs attention, and what is ready for review."],
    editions: ["Editions", "Explore sample editorial packages and their separate language and approval states."],
    stories: ["Story desk", "Track developing stories, evidence status, topics and editorial progress."],
    review: ["Review queue", "Human decisions, unresolved evidence and language checks that block release."],
    sources: ["Sources & rights", "Inspect operation-specific permission records and review dates."],
    operations: ["Operations", "A preview of worker supervision, durable checkpoints and audit visibility."]
  };
  function show(view) {
    if (!labels[view]) return;
    views.forEach(node => node.classList.toggle("active", node.id === "view-" + view));
    nav.querySelectorAll("[data-view]").forEach(node => node.classList.toggle("active", node.dataset.view === view));
    document.getElementById("crumb-current").textContent = labels[view][0];
    document.getElementById("page-title").textContent = labels[view][0];
    document.getElementById("page-subtitle").textContent = labels[view][1];
    document.querySelector(".content").scrollIntoView({behavior:"smooth",block:"start"});
    history.replaceState(null, "", "#" + view);
  }
  nav.addEventListener("click", event => {
    const button = event.target.closest("[data-view]");
    if (button) show(button.dataset.view);
  });
  document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => show(button.dataset.go)));
  document.querySelectorAll(".filter-tab").forEach(button => {
    button.addEventListener("click", () => {
      button.parentElement.querySelectorAll(".filter-tab").forEach(tab => tab.classList.remove("selected"));
      button.classList.add("selected");
      toast("This filter is a visual prototype. Live edition filtering needs the backend API.");
    });
  });
  const search = document.getElementById("story-search");
  const filter = document.getElementById("story-filter");
  function filterStories() {
    const query = search.value.trim().toLowerCase();
    const status = filter.value;
    let visible = 0;
    document.querySelectorAll("#stories-table tbody tr").forEach(row => {
      const matchesText = row.textContent.toLowerCase().includes(query);
      const matchesStatus = status === "all" || row.dataset.status === status;
      row.hidden = !(matchesText && matchesStatus);
      if (!row.hidden) visible++;
    });
    document.getElementById("stories-empty").hidden = visible !== 0;
  }
  search.addEventListener("input", filterStories);
  filter.addEventListener("change", filterStories);
  function toast(message) {
    const node = document.getElementById("toast");
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.remove("show"), 3000);
  }
  document.getElementById("dismiss-banner").addEventListener("click", () => {
    document.querySelector(".demo-banner").remove();
  });
  document.getElementById("export-button").addEventListener("click", () => {
    const snapshot = {
      product: "Sarangsho Studio UI prototype",
      environment: "demo",
      generatedAt: new Date().toISOString(),
      note: "All figures and records are illustrative. No backend is connected.",
      sections: ["overview", "editions", "stories", "review", "sources", "operations"]
    };
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], {type:"application/json"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "sarangsho-studio-demo-snapshot.json";
    link.click();
    URL.revokeObjectURL(url);
    toast("Demo snapshot exported. It contains prototype metadata only.");
  });
  const initial = location.hash.replace("#", "");
  show(labels[initial] ? initial : "overview");
})();
