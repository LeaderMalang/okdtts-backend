(function () {
  const INVOICE_DATA_BASE = "/admin/sale/salereturn/invoice-data/"; // adjust if your admin path differs

  function $(sel) { return document.querySelector(sel); }

  // Works for plain select and select2-autocomplete
  function setSelect2Value(selectEl, id, text) {
    if (!selectEl) return;
    const opt = new Option(text || String(id), String(id), true, true);
    // Clear existing for safety (admin autocomplete often keeps old placeholder)
    selectEl.innerHTML = "";
    selectEl.appendChild(opt);
    // fire native change so Django’s formset machinery notices
    selectEl.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function ensureFormRows(prefix, countNeeded) {
    const totalEl = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
    if (!totalEl) return 0;

    // Mark all existing rows for delete (clean slate)
    document.querySelectorAll(`tr.form-row.dynamic-${prefix}`).forEach(row => {
      const removeLink = row.querySelector("a.inline-deletelink");
      const delBox = row.querySelector("input[type=checkbox][name$='-DELETE']");
      if (removeLink && removeLink.click) removeLink.click();
      else if (delBox) delBox.checked = true;
    });

    // Add rows until we reach countNeeded
    let current = parseInt(totalEl.value || "0", 10);
    const addLink = document.querySelector(`#${prefix}-group .add-row a.addlink`);
    while (current < countNeeded) {
      if (addLink) addLink.click();
      current += 1;
    }
    totalEl.value = String(current);
    return current;
  }

  function fillLine(prefix, index, item) {
    function set(name, val) {
      const el = document.getElementById(`id_${prefix}-${index}-${name}`);
      if (el) el.value = val ?? "";
    }
    const qty  = Number(item.default_qty ?? item.max_return_qty ?? 0) || 0;
    const rate = Number(item.rate || 0) || 0;

    set("product", item.product_id || "");
    set("batch_number", item.batch_number || "");
    set("expiry_date", item.expiry_date || "");
    set("quantity", qty);
    set("rate", rate.toFixed(2));
    set("amount", (qty * rate).toFixed(2));
  }

  function updateTotalAmountFromItems(items) {
    const totalField = document.getElementById("id_total_amount");
    if (!totalField) return;
    const total = (items || []).reduce((s, it) => {
      const qty  = Number(it.default_qty ?? it.max_return_qty ?? 0) || 0;
      const rate = Number(it.rate || 0) || 0;
      return s + qty * rate;
    }, 0);
    totalField.value = total.toFixed(2);
  }

  function fetchInvoiceData(invoiceId) {
    return fetch(`${INVOICE_DATA_BASE}${invoiceId}/`, { credentials: "same-origin" })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
  }

  function onInvoiceChanged(invoiceId) {
    if (!invoiceId) return;
    fetchInvoiceData(invoiceId)
      .then(data => {
        // Fill customer / warehouse
        setSelect2Value($("#id_customer"),  data.customer.id,  data.customer.text);
        setSelect2Value($("#id_warehouse"), data.warehouse.id, data.warehouse.text);

        // Fill lines
        const items = data.items || [];
        ensureFormRows("items", items.length);
        items.forEach((it, idx) => fillLine("items", idx, it));
        updateTotalAmountFromItems(items);
      })
      .catch(err => console.error("SaleReturn invoice-data fetch failed:", err));
  }

  function bindInvoiceChange() {
    const sel = $("#id_invoice");
    if (!sel) return;

    // Native change (works even if select2 hides the element)
    sel.addEventListener("change", () => {
      const val = sel.value || "";
      if (val) onInvoiceChanged(val);
    });

    // Attach to Select2 events using Django's namespaced jQuery
    const $dj = (window.django && window.django.jQuery) ? window.django.jQuery : null;
    if ($dj && $dj.fn && $dj.fn.select2) {
      const $el = $dj(sel);

      // When user chooses an item
      $el.on("select2:select", function () {
        const val = this.value || "";
        if (val) onInvoiceChanged(val);
      });

      // When user clears selection
      $el.on("select2:clear", function () {
        ensureFormRows("items", 0);
        updateTotalAmountFromItems([]);
      });

      // Some admin builds fire 'select2:selecting' earlier
      $el.on("select2:selecting", function () {
        // nothing extra needed, but kept for robustness
      });
    }

    // Fallback: if admin initializes select2 after our bindings,
    // poll for selected value changes for a short window.
    let lastVal = sel.value;
    let ticks = 0;
    const poll = setInterval(() => {
      ticks += 1;
      if (sel.value !== lastVal) {
        lastVal = sel.value;
        if (lastVal) onInvoiceChanged(lastVal);
      }
      // Stop after ~10s
      if (ticks > 200) clearInterval(poll);
    }, 50);

    // Auto-load if pre-selected (as in your HTML sample)
    if (sel.value) onInvoiceChanged(sel.value);

    // Add a small manual button for debugging (optional)
    const wrapper = sel.closest(".related-widget-wrapper") || sel.parentElement;
    if (wrapper && !wrapper.querySelector(".sr-load-invoice")) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Load invoice items";
      btn.className = "button sr-load-invoice";
      btn.style.marginLeft = "8px";
      btn.addEventListener("click", () => sel.value && onInvoiceChanged(sel.value));
      wrapper.appendChild(btn);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindInvoiceChange);
  } else {
    bindInvoiceChange();
  }
})();
