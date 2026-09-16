const siteRoot = new URL("../../", import.meta.url);
const pagefindModuleUrl = new URL("pagefind/pagefind.js", siteRoot);
const searchPageUrl = new URL("search/", siteRoot);

let pagefindPromise;
let lastTrigger = null;

function icon(name) {
  if (name === "close") {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  }

  return '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4 4"/></svg>';
}

async function loadPagefind() {
  if (!pagefindPromise) {
    pagefindPromise = import(pagefindModuleUrl.href).then(async (pagefind) => {
      await pagefind.init();
      return pagefind;
    });
  }
  return pagefindPromise;
}

function resolveResultUrl(rawUrl) {
  if (!rawUrl) return siteRoot.href;

  try {
    const absolute = new URL(rawUrl);
    if (absolute.protocol === "http:" || absolute.protocol === "https:") return absolute.href;
  } catch (_) {
    // Pagefind normally returns a root-relative URL, handled below.
  }

  if (rawUrl.startsWith(siteRoot.pathname)) {
    return new URL(rawUrl, siteRoot.origin).href;
  }

  return new URL(rawUrl.replace(/^\/+/, ""), siteRoot).href;
}

function normaliseSearchText(value) {
  return (value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function textFromHtml(value) {
  const template = document.createElement("template");
  template.innerHTML = value || "";
  return template.content.textContent.trim();
}

function matchingSection(data, query) {
  const queryText = normaliseSearchText(query);
  const ignoredWords = new Set(["and", "are", "can", "does", "for", "how", "the", "what", "when", "where", "which", "who", "why", "with"]);
  const queryTerms = queryText.split(" ").filter((term) => term.length > 2 && !ignoredWords.has(term));

  if (!queryTerms.length || !Array.isArray(data.sub_results)) return null;

  return data.sub_results
    .map((section, index) => {
      const title = normaliseSearchText(section.title);
      const titleHits = queryTerms.filter((term) => title.includes(term)).length;
      const exactPhrase = queryText.length > 2 && title.includes(queryText);

      return {
        section,
        index,
        score: (exactPhrase ? 1000 : 0) + (titleHits / queryTerms.length) * 100 + titleHits,
        titleHits,
      };
    })
    .filter((candidate) => candidate.titleHits > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)[0]?.section || null;
}

function resultCard(data, query) {
  const section = matchingSection(data, query);
  const resultUrl = section?.url || data.url;
  const resultTitle = section?.title || data.meta?.title || "Untitled page";
  const sectionExcerpt = section?.excerpt || "";
  const sectionExcerptText = textFromHtml(sectionExcerpt);
  const usefulSectionExcerpt = sectionExcerptText.length > resultTitle.length + 20;
  const resultExcerpt = usefulSectionExcerpt
    ? sectionExcerpt
    : data.meta?.description || data.excerpt || "";

  const article = document.createElement("article");
  article.className = "site-search-result";

  const link = document.createElement("a");
  link.className = "site-search-result__link";
  link.href = resolveResultUrl(resultUrl);

  const type = document.createElement("span");
  type.className = "site-search-result__type";
  type.textContent = data.meta?.type || "ADHD Junction";

  const title = document.createElement("h3");
  title.textContent = resultTitle;

  const excerpt = document.createElement("p");
  excerpt.className = "site-search-result__excerpt";
  // Pagefind encodes source HTML before inserting its own safe <mark> tags.
  excerpt.innerHTML = resultExcerpt;

  const arrow = document.createElement("span");
  arrow.className = "site-search-result__arrow";
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "→";

  link.append(type, title, excerpt, arrow);
  article.append(link);
  return article;
}

function emptyState(message, suggestions = false) {
  const box = document.createElement("div");
  box.className = "site-search-empty";

  const copy = document.createElement("p");
  copy.textContent = message;
  box.append(copy);

  if (suggestions) {
    const examples = document.createElement("p");
    examples.className = "site-search-empty__examples";
    examples.textContent = "Try assessment, waiting times, preparing, medication or Right to Choose.";
    box.append(examples);
  }

  return box;
}

function bindSearch(root, options = {}) {
  const input = root.querySelector("[data-site-search-input]");
  const status = root.querySelector("[data-site-search-status]");
  const results = root.querySelector("[data-site-search-results]");
  const allResults = root.querySelector("[data-site-search-all]");
  const clear = root.querySelector("[data-site-search-clear]");
  const limit = options.limit ?? Infinity;
  let requestNumber = 0;

  async function runSearch(value, updateAddress = false) {
    const query = value.trim();
    const thisRequest = ++requestNumber;

    clear.hidden = !query;
    results.replaceChildren();
    if (allResults) allResults.hidden = true;

    if (updateAddress) {
      const url = new URL(window.location.href);
      if (query) url.searchParams.set("q", query);
      else url.searchParams.delete("q");
      history.replaceState(null, "", url);
    }

    if (!query) {
      status.textContent = "Search every ADHD Junction guide and resource.";
      results.append(emptyState("What would you like to find?", true));
      return;
    }

    status.textContent = "Searching…";

    try {
      const pagefind = await loadPagefind();
      const search = await pagefind.debouncedSearch(query, {}, 160);
      if (!search || thisRequest !== requestNumber) return;

      const total = search.results.length;
      status.textContent = total === 1 ? "1 result" : `${total} results`;

      if (!total) {
        results.append(emptyState(`Nothing matched “${query}”. Try a shorter or more general phrase.`, true));
        return;
      }

      const visibleResults = Number.isFinite(limit) ? search.results.slice(0, limit) : search.results;
      const data = await Promise.all(visibleResults.map((result) => result.data()));
      if (thisRequest !== requestNumber) return;
      data.forEach((item) => results.append(resultCard(item, query)));

      if (allResults && total > limit) {
        const destination = new URL(searchPageUrl);
        destination.searchParams.set("q", query);
        allResults.href = destination.href;
        allResults.querySelector("span:first-child").textContent = `See all ${total} results`;
        allResults.hidden = false;
      }
    } catch (error) {
      if (thisRequest !== requestNumber) return;
      console.error("ADHD Junction search could not load", error);
      status.textContent = "Search unavailable";
      results.append(emptyState("Search is temporarily unavailable. The rest of the site is still working normally."));
    }
  }

  input.addEventListener("focus", () => loadPagefind().catch(() => {}), { once: true });
  input.addEventListener("input", () => runSearch(input.value, Boolean(options.updateAddress)));
  root.querySelector("form").addEventListener("submit", (event) => event.preventDefault());

  clear.addEventListener("click", () => {
    input.value = "";
    runSearch("", Boolean(options.updateAddress));
    input.focus();
  });

  return {
    input,
    search: runSearch,
  };
}

function searchFormMarkup(idPrefix, fullPage = false) {
  return `
    <form class="site-search-form" role="search">
      <label class="visually-hidden" for="${idPrefix}-input">Search ADHD Junction</label>
      <span class="site-search-form__icon">${icon("search")}</span>
      <input
        id="${idPrefix}-input"
        type="search"
        inputmode="search"
        autocomplete="off"
        spellcheck="false"
        placeholder="Search ADHD Junction"
        data-site-search-input
      >
      <button class="site-search-form__clear" type="button" data-site-search-clear hidden>
        Clear
      </button>
    </form>
    <div class="site-search-summary" data-site-search-status aria-live="polite">
      Search every ADHD Junction guide and resource.
    </div>
    <div class="site-search-results${fullPage ? " site-search-results--page" : ""}" data-site-search-results></div>
    ${fullPage ? "" : `
      <a class="site-search-all" href="${searchPageUrl.href}" data-site-search-all hidden>
        <span>See all results</span><span aria-hidden="true">→</span>
      </a>`}
  `;
}

function createTrigger(mobile = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `site-search-trigger${mobile ? " site-search-trigger--mobile" : ""}`;
  button.dataset.siteSearchOpen = "";
  button.setAttribute("aria-label", "Search ADHD Junction");
  button.innerHTML = `${icon("search")}<span>Search</span>${mobile ? "" : "<kbd>Ctrl K</kbd>"}`;
  return button;
}

function addHeaderTriggers() {
  const desktopNav = document.querySelector(".desktop-nav");
  if (desktopNav && !desktopNav.querySelector("[data-site-search-open]")) {
    desktopNav.append(createTrigger());
  }

  const mobilePanel = document.querySelector(".mobile-menu__panel");
  if (mobilePanel && !mobilePanel.querySelector("[data-site-search-open]")) {
    const divider = document.createElement("div");
    divider.className = "mobile-menu__rule";
    divider.setAttribute("aria-hidden", "true");
    mobilePanel.append(divider, createTrigger(true));
  }
}

function bindNavigationMenus() {
  const mobileMenus = [...document.querySelectorAll(".mobile-menu")];
  if (!mobileMenus.length) return;

  const closeMenu = (menu, returnFocus = false) => {
    if (!menu.open) return;
    menu.open = false;
    const summary = menu.querySelector(":scope > summary");
    if (returnFocus) summary?.focus();
  };

  mobileMenus.forEach((menu) => {
    const summary = menu.querySelector(":scope > summary");

    menu.addEventListener("toggle", () => {
      summary?.setAttribute("aria-label", menu.open ? "Close navigation" : "Open navigation");
    });

    menu.querySelectorAll(".mobile-menu__panel a").forEach((link) => {
      link.addEventListener("click", () => closeMenu(menu));
    });
  });

  document.addEventListener("pointerdown", (event) => {
    mobileMenus.forEach((menu) => {
      if (menu.open && !menu.contains(event.target)) closeMenu(menu);
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    mobileMenus.forEach((menu) => closeMenu(menu, true));
  });

  const desktopQuery = window.matchMedia("(min-width: 901px)");
  desktopQuery.addEventListener?.("change", (event) => {
    if (event.matches) mobileMenus.forEach((menu) => closeMenu(menu));
  });
}

function createDialog() {
  const dialog = document.createElement("dialog");
  dialog.className = "site-search-dialog";
  dialog.setAttribute("aria-labelledby", "site-search-title");
  dialog.innerHTML = `
    <div class="site-search-dialog__frame">
      <header class="site-search-dialog__bar">
        <div>
          <span class="site-search-dialog__label">ADHD JUNCTION</span>
          <h2 id="site-search-title">Find what you need</h2>
        </div>
        <button class="site-search-dialog__close" type="button" aria-label="Close search">
          ${icon("close")}
        </button>
      </header>
      <div class="site-search-dialog__body">
        ${searchFormMarkup("site-search-modal")}
      </div>
    </div>`;
  document.body.append(dialog);

  const controller = bindSearch(dialog, { limit: 6 });
  const close = dialog.querySelector(".site-search-dialog__close");
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    if (lastTrigger) lastTrigger.focus();
  });

  return { dialog, controller };
}

addHeaderTriggers();
bindNavigationMenus();

const fullPageRoot = document.querySelector("[data-site-search-page]");
let modal;

if (fullPageRoot) {
  fullPageRoot.innerHTML = searchFormMarkup("site-search-page", true);
  const controller = bindSearch(fullPageRoot, { updateAddress: true });
  const initialQuery = new URL(window.location.href).searchParams.get("q") || "";
  controller.input.value = initialQuery;
  controller.search(initialQuery);
  modal = { controller };
} else {
  modal = createDialog();
}

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-site-search-open]");
  if (!trigger) return;
  lastTrigger = trigger;

  const mobileMenu = trigger.closest(".mobile-menu");
  if (mobileMenu) mobileMenu.open = false;

  if (fullPageRoot) {
    modal.controller.input.scrollIntoView({ behavior: "smooth", block: "center" });
    modal.controller.input.focus();
  } else {
    modal.dialog.showModal();
    window.requestAnimationFrame(() => modal.controller.input.focus());
  }
});

document.addEventListener("keydown", (event) => {
  const shortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k";
  if (!shortcut) return;
  event.preventDefault();

  if (fullPageRoot) {
    modal.controller.input.focus();
  } else if (!modal.dialog.open) {
    lastTrigger = document.querySelector("[data-site-search-open]");
    modal.dialog.showModal();
    window.requestAnimationFrame(() => modal.controller.input.focus());
  }
});
