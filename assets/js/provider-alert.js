(function () {
  "use strict";

  var dialog = document.getElementById("provider-alert");
  var openButton = document.querySelector("[data-provider-alert-open]");
  if (!dialog || !openButton) return;

  var closeButton = dialog.querySelector("[data-provider-alert-close]");
  var revealButton = dialog.querySelector("[data-provider-alert-reveal]");
  var form = document.getElementById("provider-alert-form");
  var email = document.getElementById("provider-alert-email");
  var submit = form && form.querySelector('button[type="submit"]');
  var setupNotice = document.getElementById("provider-alert-setup");
  var success = document.getElementById("provider-alert-success");
  var error = document.getElementById("provider-alert-error");
  var config = window.ADHD_JUNCTION_CONTACT || {};
  var basinEndpoint = String(config.basinEndpoint || "").trim();
  var turnstileSiteKey = String(config.turnstileSiteKey || "").trim();
  var isConfigured = /^https:\/\/usebasin\.com\/f\/[A-Za-z0-9_-]+$/.test(basinEndpoint) &&
    turnstileSiteKey.length > 10;

  function openDialog() {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function closeDialog() {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  openButton.addEventListener("click", openDialog);
  if (closeButton) closeButton.addEventListener("click", closeDialog);
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) closeDialog();
  });

  if (revealButton && form) {
    revealButton.addEventListener("click", function () {
      revealButton.hidden = true;
      form.hidden = false;
      if (email) email.focus();
    });
  }

  if (!form) return;

  if (!isConfigured) {
    form.addEventListener("submit", function (event) { event.preventDefault(); });
    if (setupNotice) setupNotice.hidden = false;
    if (submit) submit.disabled = true;
    return;
  }

  form.action = basinEndpoint;
  form.setAttribute("data-basin-form", "");
  form.setAttribute("data-basin-success-action", "render");
  form.setAttribute("data-basin-success-id", "provider-alert-success");
  form.setAttribute("data-basin-error-id", "provider-alert-error");
  form.setAttribute("data-basin-spam-protection", "turnstile");
  form.setAttribute("data-basin-turnstile-sitekey", turnstileSiteKey);
  form.setAttribute("data-basin-capture-url", "true");
  if (submit) submit.disabled = false;

  if (!document.querySelector('script[src^="https://js.usebasin.com/"]')) {
    var basinScript = document.createElement("script");
    basinScript.src = "https://js.usebasin.com/v2.11.1.min.js";
    basinScript.defer = true;
    document.body.appendChild(basinScript);
  }

  document.addEventListener("basinjsFormSubmitted", function (event) {
    if (event.detail && event.detail.form === form && submit) {
      submit.disabled = true;
      submit.querySelector("span").textContent = "Submitting…";
    }
  });

  document.addEventListener("basinjsFormSuccess", function (event) {
    if (event.detail && event.detail.form === form) {
      form.hidden = true;
      if (success) {
        success.style.display = "block";
        success.focus();
      }
    }
  });

  document.addEventListener("basinjsFormError", function (event) {
    if (event.detail && event.detail.form === form) {
      if (submit) {
        submit.disabled = false;
        submit.querySelector("span").textContent = "Join the notification list";
      }
      if (error) {
        error.style.display = "block";
        error.focus();
      }
    }
  });
})();
