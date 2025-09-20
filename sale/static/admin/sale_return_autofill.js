// static/admin/sale_return_autofill.js
(function () {
  "use strict";

  // --- CONFIG ---
  const INVOICE_JSON_BASE = "/admin/sale/salereturn/invoice-data/"; // trailing slash expected

  // --- HELPERS ---
  const $  = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const num = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  const isAddForm = () => /\/add\/?$/.test(location.pathname);

  function getTotalsEls() {
    return {
      totalForms: $("#id_items-TOTAL_FORMS"),
      initialForms: $("#id_items-INITIAL_FORMS"),
      addLink: $("#items-group .add-row a.addlink"),
    };
  }

  function ensureRows(countNeeded) {
    const { totalForms, addLink } = getTotalsEls();
    if (!totalForms) return 0;
    let current = parseInt(totalForms.value || "0", 10);
    while (current < countNeeded) {
      if (addLink) addLink.click();
      current += 1;
    }
    totalForms.value = String(current);
    return current;
  }

  // Select2-safe setter (works for plain selects too)
  function setSelectValue(selectEl, id, text) {
    if (!selectEl) return;
    const isSelect2 = selectEl.classList.contains("admin-autocomplete");
    if (isSelect2 && window.django && window.django.jQuery) {
      const $dj = window.django.jQuery;
      // Replace options so select2 gets a real selection
      selectEl.innerHTML = "";
      const opt = new Option(text || String(id), String(id), true, true);
      selectEl.appendChild(opt);
      $dj(selectEl).trigger("change");
    } else {
      // Plain select
      // If option isn't there, add it briefly so value sticks
      let opt = Array.from(selectEl.options).find((o) => o.value == id);
      if (!opt) {
        opt = new Option(text || String(id), String(id), true, true);
        selectEl.appendChild(opt);
      }
      selectEl.value = String(id);
      selectEl.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function setInputValue(id, val) {
    const el = document.getElementById(id);
    if (el) {
      el.value = val ?? "";
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function fillLine(index, item) {
    // Fields we expect from invoice JSON payload
    //  product_id, product_label, batch_number, expiry_date, default_qty, rate
    setSelectValue(document.getElementById(`id_items-${index}-product`), item.product_id, item.product_label);
    setInputValue(`id_items-${index}-batch_number`, item.batch_number || "");
    setInputValue(`id_items-${index}-expiry_date`, item.expiry_date || "");
    const qty = num(item.default_qty || 0);
    const rate = num(item.rate || 0);
    setInputValue(`id_items-${index}-quantity`, qty);
    setInputValue(`id_items-${index}-rate`, rate.toFixed(2));
    setInputValue(`id_items-${index}-amount`, (qty * rate).toFixed(2));
  }

  function recomputeTotalsFromRows() {
    const rows = $$('tr.form-row.dynamic-items');
    let totalAmount = 0;
    rows.forEach((row) => {
      // Skip deleted rows
      const del = row.querySelector('input[type=checkbox][name$="-DELETE"]');
      if (del && del.checked) return;

      const q = row.querySelector('input[id^="id_items-"][id$="-quantity"]');
      const r = row.querySelector('input[id^="id_items-"][id$="-rate"]');
      const a = row.querySelector('input[id^="id_items-"][id$="-amount"]');

      const qty = num(q && q.value);
      const rate = num(r && r.value);
      const amt = qty * rate;

      if (a) a.value = amt.toFixed(2);
      totalAmount += amt;
    });
    const totalField = $("#id_total_amount");
    if (totalField) totalField.value = totalAmount.toFixed(2);
  }

  function fetchInvoiceData(invoiceId) {
    return fetch(`${INVOICE_JSON_BASE}${invoiceId}/`, { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
  }

  function populateFromInvoice(invoiceId) {
    if (!invoiceId) return;
    fetchInvoiceData(invoiceId)
      .then((data) => {
        // Set customer/warehouse via Select2
        setSelectValue($("#id_customer"), data.customer.id, data.customer.text);
        setSelectValue($("#id_warehouse"), data.warehouse.id, data.warehouse.text);

        // Items
        const items = Array.isArray(data.items) ? data.items : [];

        // On CHANGE form: do NOT delete any existing rows; only add if needed
        // On ADD form: there are typically 0 rows; we just add the exact amount we need.
        const { totalForms } = getTotalsEls();
        const before = parseInt(totalForms?.value || "0", 10);

        ensureRows(items.length);

        // Fill rows starting at 0; if you already had some rows on change form,
        // we only overwrite the first N rows with invoice data (never delete extras).
        items.forEach((it, idx) => fillLine(idx, it));

        // Recalc totals from item rows (Sale Return items drive totals)
        recomputeTotalsFromRows();
      })
      .catch((err) => console.error("SaleReturn invoice-data fetch failed:", err));
  }

  function bindInvoiceChange() {
    const inv = $("#id_invoice");
    if (!inv) return;

    // Native change works even with select2 wrapping
    inv.addEventListener("change", () => {
      const val = inv.value || "";
      if (val) populateFromInvoice(val);
    });

    // Select2 events via Django's jQuery
    const $dj = window.django && window.django.jQuery ? window.django.jQuery : null;
    if ($dj) {
      const $inv = $dj(inv);
      // user picked an invoice
      $inv.on("select2:select", function () {
        const val = this.value || "";
        if (val) populateFromInvoice(val);
      });
      // user cleared selection: do nothing destructive; leave form as-is
      $inv.on("select2:clear", function () {
        // no-op by design (we don't delete rows on change form)
      });
    }

    // On ADD form with a preselected value (rare), load once:
    if (isAddForm() && inv.value) {
      populateFromInvoice(inv.value);
    }
  }

  function bindLiveTotals() {
    // Any edit to qty/rate/amount should recompute total_amount
    document.addEventListener("input", (e) => {
      if (
        e.target &&
        (e.target.id.endsWith("-quantity") ||
         e.target.id.endsWith("-rate") ||
         e.target.id.endsWith("-amount")) &&
        e.target.id.startsWith("id_items-")
      ) {
        recomputeTotalsFromRows();
      }
    });
  }

  function init() {
    bindInvoiceChange();
    bindLiveTotals();
    // Initial compute (e.g., after server-side validation or re-render)
    recomputeTotalsFromRows();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
