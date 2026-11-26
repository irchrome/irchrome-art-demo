/* Progressive enhancement only: mobile nav keyboard escape + gallery lightbox.
   All links, navigation (<details>), and full-resolution images work without JS. */
(function () {
  "use strict";

  // Close native <details> mobile menu on Escape or link click
  var navDetails = document.querySelector(".nav-mobile");
  if (navDetails) {
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && navDetails.hasAttribute("open")) {
        navDetails.removeAttribute("open");
      }
    });
    navDetails.addEventListener("click", function (e) {
      if (e.target && e.target.closest("a")) {
        navDetails.removeAttribute("open");
      }
    });
  }

  // Lightbox for series gallery plates
  var links = Array.prototype.slice.call(
    document.querySelectorAll("a[data-lightbox]")
  );
  if (!links.length || typeof HTMLDialogElement === "undefined") {
    return;
  }

  var dialog = document.createElement("dialog");
  dialog.className = "lightbox";
  dialog.setAttribute("aria-label", "Photograph viewer");
  dialog.innerHTML =
    '<div class="lightbox__inner">' +
    '  <div class="lightbox__top">' +
    '    <span class="eyebrow" data-lb-counter></span>' +
    '    <button type="button" class="btn btn--ghost" data-lb-close>Close</button>' +
    "  </div>" +
    '  <div class="lightbox__stage"><img alt="" data-lb-img></div>' +
    '  <div class="lightbox__bottom">' +
    '    <p class="lightbox__caption" data-lb-caption></p>' +
    '    <div class="lightbox__controls">' +
    '      <button type="button" class="btn btn--ghost" data-lb-prev>Prev</button>' +
    '      <button type="button" class="btn btn--ghost" data-lb-next>Next</button>' +
    "    </div>" +
    "  </div>" +
    "</div>";
  document.body.appendChild(dialog);

  var imgEl = dialog.querySelector("[data-lb-img]");
  var capEl = dialog.querySelector("[data-lb-caption]");
  var cntEl = dialog.querySelector("[data-lb-counter]");
  var current = 0;

  function show(index) {
    current = (index + links.length) % links.length;
    var a = links[current];
    imgEl.src = a.getAttribute("href");
    imgEl.alt = a.getAttribute("data-alt") || "";
    capEl.textContent = a.getAttribute("data-caption") || a.getAttribute("data-alt") || "";
    cntEl.textContent = "Plate " + (current + 1) + " / " + links.length;
  }

  links.forEach(function (a, idx) {
    a.addEventListener("click", function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      show(idx);
      dialog.showModal();
    });
  });

  dialog.querySelector("[data-lb-close]").addEventListener("click", function () {
    dialog.close();
  });
  dialog.querySelector("[data-lb-prev]").addEventListener("click", function () {
    show(current - 1);
  });
  dialog.querySelector("[data-lb-next]").addEventListener("click", function () {
    show(current + 1);
  });
  dialog.addEventListener("keydown", function (e) {
    if (e.key === "ArrowLeft") show(current - 1);
    if (e.key === "ArrowRight") show(current + 1);
  });
  dialog.addEventListener("click", function (e) {
    if (e.target === dialog) dialog.close();
  });
})();
