(() => {
  const nav = document.getElementById("primary-nav");
  const pageTitle = document.getElementById("page-title");
  const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
  const views = [...document.querySelectorAll(".view")];
  const labels = {
    overview: ["Editorial overview", "A clear view of what is moving, what needs attention, and what is ready for review."],
    editions: ["Editions", "Explore sample editorial packages and their separate language and approval states."],
    stories: ["Story desk", "Track developing stories, evidence status, topics and editorial progress."],
    review: ["Review queue", "Human decisions, unresolved evidence and language checks that block release."],
    sources: ["Sources & rights", "Inspect operation-specific permission records and review dates."],
    operations: ["Operations", "A preview of worker supervision, durable checkpoints and audit visibility."],
    release: ["Release control", "Inspect preflight blockers and the boundaries between approval, authorization and publication."]
  };
  function show(view, options = {}) {
    if (!labels[view]) return;
    const focusHeading = options.focusHeading !== false;
    const historyMode = options.historyMode || "push";
    views.forEach(node => node.classList.toggle("active", node.id === "view-" + view));
    nav.querySelectorAll("[data-view]").forEach(node => {
      const selected = node.dataset.view === view;
      node.classList.toggle("active", selected);
      node.setAttribute("aria-current", selected ? "page" : "false");
    });
    document.getElementById("crumb-current").textContent = labels[view][0];
    pageTitle.textContent = labels[view][0];
    document.getElementById("page-subtitle").textContent = labels[view][1];
    if (focusHeading) pageTitle.focus({preventScroll:true});
    document.querySelector(".content").scrollIntoView({behavior:motionPreference.matches ? "auto" : "smooth",block:"start"});
    if (historyMode !== "none" && location.hash !== "#" + view) {
      if (historyMode === "replace") history.replaceState(null, "", "#" + view);
      else history.pushState(null, "", "#" + view);
    }
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
    document.getElementById("story-results-status").textContent = visible === 1 ? "Showing 1 sample story." : "Showing " + visible + " sample stories.";
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

  const storyDetails = {
    "S-024": {title:"Central bank policy decision", topic:"Economy · Evidence review", summary:"Sample story concerning a central bank policy decision. Conflicting reported figures remain unresolved, so the story must stay in evidence review.", en:"Draft · fact check blocked", bn:"Not approved · translation pending", source:"Four illustrative observations are attached. The reported values conflict; an editor must compare publication time, attribution, scope and the original statement before a claim can be cleared.", claims:["C-204 · Policy rate: verify against the official decision notice.","C-205 · Effective date: confirm the date and time zone.","C-206 · Conflicting figure: unresolved; do not publish as a settled fact."]},
    "S-023": {title:"Coastal resilience programme", topic:"Climate · Editorial draft", summary:"Sample story about a coastal resilience programme. The evidence links are represented as fixture data and have not been independently verified.", en:"Draft · editorial review", bn:"Not started · independent review required", source:"Three illustrative observations are recorded. Check programme name, funding period and geographic scope against primary documentation.", claims:["C-193 · Programme scope: source comparison required.","C-194 · Funding period: confirm fiscal-year boundaries."]},
    "S-022": {title:"Public health budget allocation", topic:"Health · Ready for approval", summary:"Sample story about a public health budget allocation. The prototype status is not a real editorial approval.", en:"Ready for human approval", bn:"Language review marked complete · separate approval required", source:"Five illustrative observations are represented. Verify the budget document, allocation category and whether amounts are proposed or approved.", claims:["C-181 · Allocation amount: check the approved budget document.","C-182 · Status: distinguish proposal from final appropriation."]},
    "S-019": {title:"Regional transport corridor", topic:"Infrastructure · Evidence review", summary:"Sample story about a regional transport corridor. Attribution is not sufficiently clear to support publication.", en:"Draft · attribution blocked", bn:"Not started · do not inherit English status", source:"Two illustrative observations are attached. The cited locator does not identify the responsible speaker or document clearly enough.", claims:["C-118 · Attribution: identify the original speaker or issuing body.","C-119 · Route description: confirm endpoints against the primary source."]}
  };
  const observations = {
  "S-024": [
    {
      "claim": "C-206 · Reported total",
      "ref": "OBS-024-A",
      "value": "18 (fictional)",
      "attribution": "Sample Publisher A · article body",
      "assessment": "Conflicts with OBS-024-B",
      "kind": "conflict"
    },
    {
      "claim": "C-206 · Reported total",
      "ref": "OBS-024-B",
      "value": "23 (fictional)",
      "attribution": "Sample Publisher B · live update",
      "assessment": "Conflicts with OBS-024-A",
      "kind": "conflict"
    },
    {
      "claim": "C-204 · Policy rate",
      "ref": "OBS-024-C",
      "value": "Not stated",
      "attribution": "Sample Publisher C · headline only",
      "assessment": "Insufficient for claim",
      "kind": "warning"
    },
    {
      "claim": "C-205 · Effective date",
      "ref": "OBS-024-D",
      "value": "Date not verified",
      "attribution": "Unverified secondary reference",
      "assessment": "Primary record required",
      "kind": "warning"
    }
  ],
  "S-023": [
    {
      "claim": "C-193 · Programme scope",
      "ref": "OBS-023-A",
      "value": "Coastal districts (unverified)",
      "attribution": "Sample Publisher A · article body",
      "assessment": "Needs primary-source check",
      "kind": "warning"
    },
    {
      "claim": "C-193 · Programme scope",
      "ref": "OBS-023-B",
      "value": "Region not specified",
      "attribution": "Sample Publisher B · summary",
      "assessment": "Scope remains unclear",
      "kind": "warning"
    },
    {
      "claim": "C-194 · Funding period",
      "ref": "OBS-023-C",
      "value": "Fiscal period not stated",
      "attribution": "Sample Publisher C · article body",
      "assessment": "Do not infer period",
      "kind": "warning"
    }
  ],
  "S-022": [
    {
      "claim": "C-181 · Allocation amount",
      "ref": "OBS-022-A",
      "value": "Amount omitted from fixture",
      "attribution": "Sample Publisher A · budget report",
      "assessment": "Check primary budget",
      "kind": "warning"
    },
    {
      "claim": "C-181 · Allocation amount",
      "ref": "OBS-022-B",
      "value": "Different scope; not comparable",
      "attribution": "Sample Publisher B · analysis",
      "assessment": "Do not combine totals",
      "kind": "conflict"
    },
    {
      "claim": "C-182 · Approval status",
      "ref": "OBS-022-C",
      "value": "Proposed / approved unclear",
      "attribution": "Sample Publisher C · report",
      "assessment": "Status not established",
      "kind": "warning"
    }
  ],
  "S-019": [
    {
      "claim": "C-118 · Attribution",
      "ref": "OBS-019-A",
      "value": "Speaker not identified",
      "attribution": "Sample Publisher A · quoted passage",
      "assessment": "Attribution blocked",
      "kind": "warning"
    },
    {
      "claim": "C-119 · Route endpoints",
      "ref": "OBS-019-B",
      "value": "Endpoints not verified",
      "attribution": "Sample Publisher B · map reference",
      "assessment": "Primary project document required",
      "kind": "warning"
    }
  ]
};
  const decisionHistory = {
  "S-024": [
    [
      "09:10",
      "Story created",
      "Sample ingestion event"
    ],
    [
      "09:14",
      "Evidence attached",
      "Four fictional observations linked"
    ],
    [
      "09:18",
      "Conflict flagged",
      "Claim C-206 held for human review"
    ]
  ],
  "S-023": [
    [
      "08:45",
      "Story created",
      "Sample ingestion event"
    ],
    [
      "08:52",
      "Evidence linked",
      "Three fictional observations attached"
    ],
    [
      "09:02",
      "Draft opened",
      "Primary-source checks outstanding"
    ]
  ],
  "S-022": [
    [
      "18:20",
      "Story created",
      "Sample ingestion event"
    ],
    [
      "18:31",
      "Language review marked",
      "English and Bangla states kept separate"
    ],
    [
      "18:40",
      "Approval gate reached",
      "Human approval still required"
    ]
  ],
  "S-019": [
    [
      "16:05",
      "Story created",
      "Sample ingestion event"
    ],
    [
      "16:12",
      "Claim flagged",
      "Attribution for C-118 unclear"
    ]
  ]
};
  const dialog = document.getElementById("story-detail-dialog");
  function openStory(storyId) {
    const story = storyDetails[storyId];
    if (!story) return;
    document.getElementById("story-dialog-title").textContent = story.title;
    document.getElementById("story-dialog-subtitle").textContent = storyId + " · " + story.topic;
    document.getElementById("story-dialog-summary").textContent = story.summary;
    document.getElementById("story-en-state").textContent = story.en;
    document.getElementById("story-bn-state").textContent = story.bn;
    document.getElementById("story-source-note").textContent = story.source;
    const observationRows = document.getElementById("story-observation-rows");
    observationRows.replaceChildren(...(observations[storyId] || []).map(record => {
      const row = document.createElement("tr");
      [record.claim, record.ref, record.value, record.attribution].forEach(value => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      });
      const assessment = document.createElement("td");
      const tag = document.createElement("span");
      tag.className = "evidence-assessment " + record.kind;
      tag.textContent = record.assessment;
      assessment.appendChild(tag);
      row.appendChild(assessment);
      return row;
    }));
    const history = document.getElementById("story-decision-history");
    history.replaceChildren(...(decisionHistory[storyId] || []).map(([time, title, detail]) => {
      const item = document.createElement("li");
      const stamp = document.createElement("time");
      stamp.textContent = time;
      const content = document.createElement("div");
      const heading = document.createElement("b");
      heading.textContent = title;
      const note = document.createElement("span");
      note.textContent = detail;
      content.append(heading, note);
      item.append(stamp, content);
      return item;
    }));
    const list = document.getElementById("story-claim-list");
    list.replaceChildren(...story.claims.map(claim => {
      const item = document.createElement("li");
      item.textContent = claim;
      return item;
    }));
    if (typeof dialog.showModal === "function") dialog.showModal();
    else toast("Your browser does not support the story detail dialog.");
  }
  document.querySelectorAll(".story-open").forEach(button => button.addEventListener("click", () => openStory(button.dataset.storyId)));
  document.getElementById("story-dialog-close").addEventListener("click", () => dialog.close());
  document.getElementById("story-dialog-done").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });

  // Edition composition fixtures are local, illustrative and non-persistent.
  const editionStories = [
    {id:"S-024",title:"Central bank policy decision",en:"Draft · fact check blocked",bn:"Translation pending",evidence:2,rights:0,claimsResolved:false,languagesReviewed:false,rightsCleared:false},
    {id:"S-023",title:"Coastal resilience programme",en:"Editorial review",bn:"Not started",evidence:2,rights:1,claimsResolved:false,languagesReviewed:false,rightsCleared:false},
    {id:"S-022",title:"Public health budget allocation",en:"Ready for human approval",bn:"Language review complete",evidence:0,rights:0,claimsResolved:true,languagesReviewed:false,rightsCleared:false},
    {id:"S-019",title:"Regional transport corridor",en:"Attribution blocked",bn:"Not started",evidence:1,rights:0,claimsResolved:false,languagesReviewed:false,rightsCleared:false}
  ];
  let editionOrder = editionStories.map(story => story.id);
  const storyById = Object.fromEntries(editionStories.map(story => [story.id, story]));
  const storyList = document.getElementById("edition-story-list");
  const readinessList = document.getElementById("edition-readiness");
  function renderEditionComposition() {
    storyList.replaceChildren(...editionOrder.map((id, index) => {
      const story = storyById[id];
      const item = document.createElement("li"); item.className = "composition-item";
      const position = document.createElement("span"); position.className = "composition-position"; position.textContent = String(index + 1).padStart(2, "0");
      const main = document.createElement("div"); main.className = "composition-story";
      const heading = document.createElement("strong"); heading.textContent = story.title;
      const idLine = document.createElement("small"); idLine.textContent = story.id; main.append(heading, idLine);
      const variants = document.createElement("div"); variants.className = "composition-variants";
      [["EN",story.en],["BN",story.bn]].forEach(([language,state]) => { const tag=document.createElement("span");tag.className="variant-state";const label=document.createElement("b");label.textContent=language;const value=document.createElement("small");value.textContent=state;tag.append(label,value);variants.appendChild(tag); });
      const blockers = document.createElement("span"); blockers.className="composition-blockers"; blockers.textContent=story.evidence+" evidence · "+story.rights+" rights";
      const actions=document.createElement("div");actions.className="order-actions";
      [["up","Move up",index===0],["down","Move down",index===editionOrder.length-1]].forEach(([direction,label,disabled])=>{const button=document.createElement("button");button.type="button";button.className="order-button";button.dataset.move=direction;button.dataset.storyId=id;button.textContent=direction==="up"?"↑":"↓";button.setAttribute("aria-label",label+": "+story.title);button.disabled=disabled;actions.appendChild(button);});
      item.append(position,main,variants,blockers,actions);return item;
    }));
    document.getElementById("composition-count").textContent=editionOrder.length+" stories in sequence";
    const checks=[
      ["Claim evidence resolved for every included story",editionStories.every(story=>story.claimsResolved),"Unresolved claims or evidence conflicts remain."],
      ["English and Bangla variants independently reviewed",editionStories.every(story=>story.languagesReviewed),"At least one language variant still needs review."],
      ["Required source permissions verified for each operation",editionStories.every(story=>story.rightsCleared),"Rights are unknown or not cleared for at least one story."],
      ["Authorized human approval and durable audit prerequisites",false,"Backend role authorization, atomic state transition and audit guarantees are not connected."]
    ];
    readinessList.replaceChildren(...checks.map(([label,passed,detail])=>{const item=document.createElement("li");item.className=passed?"check-pass":"check-blocked";const mark=document.createElement("span");mark.setAttribute("aria-hidden","true");mark.textContent=passed?"✓":"!";const text=document.createElement("div");const title=document.createElement("strong");title.textContent=label;const note=document.createElement("small");note.textContent=passed?"Fixture check passed.":detail;text.append(title,note);item.append(mark,text);return item;}));
    document.getElementById("readiness-status").textContent=checks.every(([,passed])=>passed)?"Ready for approval":"Blocked";
  }
  storyList.addEventListener("click",event=>{const button=event.target.closest("[data-move]");if(!button||button.disabled)return;const id=button.dataset.storyId;const from=editionOrder.indexOf(id);const to=from+(button.dataset.move==="up"?-1:1);if(from<0||to<0||to>=editionOrder.length)return;[editionOrder[from],editionOrder[to]]=[editionOrder[to],editionOrder[from]];renderEditionComposition();toast(id+" moved locally. This order has not been saved.");storyList.querySelector('[data-story-id="'+id+'"][data-move="'+button.dataset.move+'"]')?.focus();});
  document.getElementById("reset-edition-order").addEventListener("click",()=>{editionOrder=editionStories.map(story=>story.id);renderEditionComposition();toast("Sample edition order reset locally.");});
  renderEditionComposition();

  // This report is derived from fictional fixtures and never authorizes a command.
  function renderReleasePreflight() {
    const gates = [
      ["Evidence and claim resolution", editionStories.every(story => story.claimsResolved), "At least one included story has unresolved evidence or attribution."],
      ["English and Bangla review", editionStories.every(story => story.languagesReviewed), "Both language variants must be reviewed independently."],
      ["Operation-specific source rights", editionStories.every(story => story.rightsCleared), "At least one source permission is unknown or uncleared."],
      ["Approved immutable edition revision", false, "No authenticated editor approval exists for a versioned edition snapshot."],
      ["Release authorization and audit readiness", false, "No server-enforced release authority, atomic command or durable audit record is connected."]
    ];
    const list = document.getElementById("release-gate-list");
    list.replaceChildren(...gates.map(([label, passed, detail], index) => {
      const item = document.createElement("li");
      item.className = passed ? "release-gate-pass" : "release-gate-blocked";
      const number = document.createElement("span");
      number.className = "release-gate-number";
      number.textContent = String(index + 1).padStart(2, "0");
      const body = document.createElement("div");
      const heading = document.createElement("strong");
      heading.textContent = label;
      const note = document.createElement("small");
      note.textContent = passed ? "Fixture check passed; this is not a live verification." : detail;
      body.append(heading, note);
      const status = document.createElement("b");
      status.className = "release-gate-status";
      status.textContent = passed ? "PASS" : "BLOCKED";
      item.append(number, body, status);
      return item;
    }));
    const passed = gates.filter(([, ok]) => ok).length;
    document.getElementById("release-gates-count").textContent = passed + " / " + gates.length;
    document.getElementById("release-blocker-count").textContent = String(gates.length - passed);
    document.getElementById("release-overall-status").textContent = passed === gates.length ? "Preflight passed" : "Blocked";
  }
  renderReleasePreflight();

  window.addEventListener("popstate", () => {
    const destination = location.hash.slice(1);
    show(labels[destination] ? destination : "overview", {historyMode:"none"});
  });
  const initial = location.hash.slice(1);
  show(labels[initial] ? initial : "overview", {
    focusHeading:false,
    historyMode: initial ? "none" : "replace"
  });
})();
