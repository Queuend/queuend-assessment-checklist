/* NHS ADHD card and revision-log renderer. No figures are hardcoded here. */
(function () {
  "use strict";

  var CARD = document.getElementById("nhs-feed");
  var CHANGE_LOG = document.getElementById("nhs-change-log");
  var DIALOG = document.getElementById("nhs-explainer-dialog");
  var OVERDUE_DAYS = 7;
  var requests = {};

  if (!CARD && !CHANGE_LOG && !DIALOG) return;

  function fetchData(source) {
    if (!requests[source]) {
      requests[source = source] = fetch(source, { cache: "no-cache" }).then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      });
    }
    return requests[source];
  }

  function findBySlug(observations, slug) {
    for (var index = 0; index < observations.length; index += 1) {
      if (observations[index].slug === slug) return observations[index];
    }
    return null;
  }

  function number(value) {
    return Number(value).toLocaleString("en-GB");
  }

  function valueOrRange(item, prefix) {
    var minimum = Number(item[prefix + "_value_min"]);
    var maximum = Number(item[prefix + "_value_max"]);
    if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) {
      return number(item[prefix + "_value"]);
    }
    return minimum === maximum ? number(minimum) : number(minimum) + "–" + number(maximum);
  }

  function shortDate(iso) {
    if (!iso) return null;
    var parsed = new Date(iso);
    if (isNaN(parsed.getTime())) return null;
    return parsed.toLocaleDateString("en-GB", {
      day: "numeric", month: "short", year: "numeric", timeZone: "Europe/London"
    });
  }

  function periodLabel(period, includeYear) {
    var match = /^(\d{4})-(\d{2})$/.exec(String(period || ""));
    if (!match) return period || "";
    var parsed = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
    var options = { month: "long", timeZone: "UTC" };
    if (includeYear) options.year = "numeric";
    return parsed.toLocaleDateString("en-GB", options);
  }

  function daysSince(iso) {
    if (!iso) return Infinity;
    var parsed = new Date(iso);
    if (isNaN(parsed.getTime())) return Infinity;
    return Math.max(0, (Date.now() - parsed.getTime()) / 86400000);
  }

  function setText(root, selector, text) {
    var element = root.querySelector(selector);
    if (element) element.textContent = text;
  }

  function unavailable(message) {
    CARD.setAttribute("data-state", "unavailable");
    setText(CARD, "[data-slot='badge']", "NHS data");
    setText(CARD, "[data-slot='fallback']", message);
  }

  function renderCard(data) {
    var current = data && data.current;
    var feed = (data && data.feed) || {};
    var observations = current && current.observations;

    if (!Array.isArray(observations) || observations.length === 0) {
      unavailable("No validated NHS figures are available yet.");
      return;
    }

    var newReferrals = findBySlug(observations, "new_referrals");
    var openReferrals = findBySlug(observations, "open_referrals");
    if (!newReferrals || !openReferrals ||
        !Number.isFinite(Number(newReferrals.value)) ||
        !Number.isFinite(Number(openReferrals.value))) {
      unavailable("The latest NHS figures are unavailable at the moment.");
      return;
    }

    setText(CARD, "[data-slot='new-value']", number(newReferrals.value));
    var qualifier = CARD.querySelector("[data-slot='new-qualifier']");
    if (qualifier) qualifier.hidden = newReferrals.qualifier !== "up_to";
    setText(
      CARD,
      "[data-slot='new-caption']",
      "new referrals for a possible ADHD assessment were recorded in " +
        periodLabel(newReferrals.data_period, false)
    );
    setText(CARD, "[data-slot='open-value']", number(openReferrals.value));
    setText(CARD, "[data-slot='period']", "Data for " + periodLabel(newReferrals.data_period, true));

    var published = shortDate(current.publication_date);
    setText(
      CARD,
      "[data-slot='published']",
      published ? "Published " + published + " · NHS England" : "Source · NHS England"
    );

    var attempted = feed.last_attempted_at;
    var successful = feed.last_successful_at;
    var checked = shortDate(successful);
    var status = feed.data_status || "review_required";
    var overdue = daysSince(attempted) > OVERDUE_DAYS;
    var state = "current";
    var badge = "Automatic update";
    var checkedPrefix = "Source checked ";

    if (overdue) {
      state = "overdue";
      badge = "Automatic check delayed";
      checkedPrefix = "Last successful check ";
    } else if (status === "review_required") {
      state = "review";
      badge = "New data awaiting review";
      checkedPrefix = "Last successful check ";
    } else if (status === "fetch_failed") {
      state = "error";
      badge = "Automatic check failed";
      checkedPrefix = "Last successful check ";
    } else if (status !== "current") {
      state = "review";
      badge = "Update awaiting validation";
      checkedPrefix = "Last successful check ";
    }

    CARD.setAttribute("data-state", state);
    setText(CARD, "[data-slot='badge']", badge);
    setText(
      CARD,
      "[data-slot='checked']",
      checked ? checkedPrefix + checked : "Successful check date unavailable"
    );
  }

  function revisionLabel(slug) {
    return slug === "new_referrals" ? "New referrals" : "Open referrals";
  }

  function renderChangeLog(changeLog, data) {
    var changes = Array.isArray(data && data.changes) ? data.changes : [];
    var revisions = changes.filter(function (item) { return item.kind === "revision"; });
    changeLog.textContent = "";

    if (!revisions.length) {
      var empty = document.createElement("p");
      empty.className = "revision-log__empty";
      empty.textContent = "No revisions have been recorded since tracking began.";
      changeLog.appendChild(empty);
      return;
    }

    var list = document.createElement("ol");
    list.className = "revision-log__list";
    revisions.forEach(function (change) {
      var item = document.createElement("li");
      item.className = "revision-log__item";

      var heading = document.createElement("strong");
      heading.textContent = revisionLabel(change.slug) + " · " +
        periodLabel(change.data_period, true);
      item.appendChild(heading);

      var values = document.createElement("span");
      values.className = "revision-log__values";
      values.textContent = valueOrRange(change, "previous") + " → " +
        valueOrRange(change, "new");
      item.appendChild(values);

      var metadata = document.createElement("span");
      metadata.className = "revision-log__meta";
      var observed = shortDate(change.observed_at);
      metadata.textContent = observed ? "Detected " + observed : "Detected in a later release";
      if (change.source_url) {
        metadata.appendChild(document.createTextNode(" · "));
        var link = document.createElement("a");
        link.href = change.source_url;
        link.textContent = "source release";
        metadata.appendChild(link);
      }
      item.appendChild(metadata);
      list.appendChild(item);
    });
    changeLog.appendChild(list);
  }

  function loadChangeLog(changeLog) {
    fetchData(changeLog.getAttribute("data-src") || "../assets/data/nhs-adhd.json")
      .then(function (data) {
        renderChangeLog(changeLog, data);
      })
      .catch(function () {
        changeLog.textContent = "The revision record is unavailable at the moment.";
      });
  }

  function rewriteDocumentUrls(root, baseUrl) {
    Array.prototype.forEach.call(root.querySelectorAll("[href]"), function (element) {
      var value = element.getAttribute("href");
      if (!value || value.charAt(0) === "#") return;
      try {
        element.setAttribute("href", new URL(value, baseUrl).href);
      } catch (error) {
        /* Keep the page's original fallback value if it cannot be resolved. */
      }
    });

    Array.prototype.forEach.call(root.querySelectorAll("[src]"), function (element) {
      var value = element.getAttribute("src");
      if (!value) return;
      try {
        element.setAttribute("src", new URL(value, baseUrl).href);
      } catch (error) {
        /* Keep the page's original fallback value if it cannot be resolved. */
      }
    });

    Array.prototype.forEach.call(root.querySelectorAll("[data-src]"), function (element) {
      var value = element.getAttribute("data-src");
      if (!value) return;
      try {
        element.setAttribute("data-src", new URL(value, baseUrl).href);
      } catch (error) {
        /* Keep the page's original fallback value if it cannot be resolved. */
      }
    });
  }

  function initialiseDialog() {
    var openLink = document.querySelector("[data-nhs-dialog-open]");
    var closeButton = DIALOG && DIALOG.querySelector("[data-nhs-dialog-close]");
    var scrollPane = DIALOG && DIALOG.querySelector("[data-nhs-dialog-scroll]");

    if (!openLink || !closeButton || !scrollPane ||
        typeof DIALOG.showModal !== "function" ||
        typeof window.DOMParser !== "function") return;

    var source = openLink.href;
    var loaded = false;
    var loading = false;
    var lastTrigger = null;

    function showError() {
      scrollPane.textContent = "";
      var error = document.createElement("div");
      error.className = "nhs-modal__error";

      var message = document.createElement("p");
      message.appendChild(document.createTextNode(
        "The explanation could not be loaded here. "
      ));
      var link = document.createElement("a");
      link.href = source;
      link.textContent = "Open the full page instead.";
      message.appendChild(link);

      error.appendChild(message);
      scrollPane.appendChild(error);
    }

    function loadDocument() {
      if (loaded || loading) return;
      loading = true;

      fetch(source, { cache: "no-cache" })
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.text().then(function (markup) {
            return { markup: markup, url: response.url || source };
          });
        })
        .then(function (result) {
          var parsed = new window.DOMParser().parseFromString(result.markup, "text/html");
          var pageMain = parsed.querySelector("main");
          if (!pageMain) throw new Error("Explanation page has no main content");

          var documentShell = document.createElement("div");
          documentShell.className = "nhs-modal__document";

          Array.prototype.forEach.call(pageMain.childNodes, function (node) {
            documentShell.appendChild(node.cloneNode(true));
          });

          Array.prototype.forEach.call(documentShell.querySelectorAll("script"), function (script) {
            script.remove();
          });
          rewriteDocumentUrls(documentShell, result.url);

          scrollPane.textContent = "";
          scrollPane.appendChild(documentShell);
          scrollPane.scrollTop = 0;

          var embeddedLog = documentShell.querySelector("#nhs-change-log");
          if (embeddedLog) loadChangeLog(embeddedLog);

          loaded = true;
          loading = false;
        })
        .catch(function () {
          loading = false;
          showError();
        });
    }

    openLink.addEventListener("click", function (event) {
      if (event.defaultPrevented ||
          (event.button !== undefined && event.button !== 0) ||
          event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      event.preventDefault();
      lastTrigger = openLink;
      document.body.classList.add("nhs-modal-open");
      if (!DIALOG.open) DIALOG.showModal();
      loadDocument();
    });

    closeButton.addEventListener("click", function () {
      DIALOG.close();
    });

    DIALOG.addEventListener("click", function (event) {
      if (event.target !== DIALOG) return;
      var bounds = DIALOG.getBoundingClientRect();
      var outside = event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom;
      if (outside) DIALOG.close();
    });

    DIALOG.addEventListener("close", function () {
      document.body.classList.remove("nhs-modal-open");
      if (lastTrigger) lastTrigger.focus();
    });

    scrollPane.addEventListener("click", function (event) {
      var link = event.target.closest && event.target.closest("a[href^='#']");
      if (!link) return;
      var target = document.getElementById(link.getAttribute("href").slice(1));
      if (!target || !DIALOG.contains(target)) return;
      event.preventDefault();
      var reduceMotion = window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    });
  }

  if (CARD) {
    fetchData(CARD.getAttribute("data-src") || "assets/data/nhs-adhd.json")
      .then(renderCard)
      .catch(function () {
        unavailable("The latest NHS figures are unavailable at the moment.");
      });
  }

  if (CHANGE_LOG) {
    loadChangeLog(CHANGE_LOG);
  }

  if (DIALOG) {
    initialiseDialog();
  }
}());
