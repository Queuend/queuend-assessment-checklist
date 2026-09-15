(function () {
  "use strict";

  var form = document.getElementById("adhd-junction-contact-form");
  if (!form) return;

  var message = document.getElementById("message");
  var count = document.getElementById("message-count");
  var submit = form.querySelector('button[type="submit"]');
  var setupNotice = document.getElementById("contact-setup-notice");
  var config = window.ADHD_JUNCTION_CONTACT || {};
  var basinEndpoint = String(config.basinEndpoint || "").trim();
  var turnstileSiteKey = String(config.turnstileSiteKey || "").trim();
  var isConfigured = /^https:\/\/usebasin\.com\/f\/[A-Za-z0-9_-]+$/.test(basinEndpoint) &&
    turnstileSiteKey.length > 10;

  function updateCount() {
    if (!message || !count) return;
    count.textContent = message.value.length.toLocaleString("en-GB") + " / 4,000";
  }

  if (message) {
    message.addEventListener("input", updateCount);
    updateCount();
  }

  if (!isConfigured) {
    form.addEventListener("submit", function (event) { event.preventDefault(); });
    form.setAttribute("aria-describedby", "contact-setup-notice");
    if (submit) submit.disabled = true;
    if (setupNotice) setupNotice.hidden = false;
    return;
  }

  form.action = basinEndpoint;
  form.setAttribute("data-basin-form", "");
  form.setAttribute("data-basin-success-action", "render");
  form.setAttribute("data-basin-success-id", "contact-success");
  form.setAttribute("data-basin-error-id", "contact-error");
  form.setAttribute("data-basin-spam-protection", "turnstile");
  form.setAttribute("data-basin-turnstile-sitekey", turnstileSiteKey);
  form.setAttribute("data-basin-capture-url", "true");
  if (submit) submit.disabled = false;

  var basinScript = document.createElement("script");
  basinScript.src = "https://js.usebasin.com/v2.11.1.min.js";
  basinScript.defer = true;
  document.body.appendChild(basinScript);

  document.addEventListener("basinjsFormSubmitted", function (event) {
    if (event.detail && event.detail.form === form && submit) {
      submit.disabled = true;
      submit.querySelector("span").textContent = "Sending…";
    }
  });

  document.addEventListener("basinjsFormSuccess", function (event) {
    if (event.detail && event.detail.form === form) {
      form.hidden = true;
      document.getElementById("contact-success").focus();
    }
  });

  document.addEventListener("basinjsFormError", function (event) {
    if (event.detail && event.detail.form === form && submit) {
      submit.disabled = false;
      submit.querySelector("span").textContent = "Send to ADHD Junction";
      document.getElementById("contact-error").focus();
    }
  });
})();
