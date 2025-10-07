(function() {
  function toNum(v) {
    var n = parseFloat((v || "").toString().replace(/,/g, ""));
    return isNaN(n) ? 0 : n;
  }

  function recalcRow(tr) {
    const qtyInput = tr.querySelector('input[name$="-quantity"]');
    const priceInput = tr.querySelector('input[name$="-purchase_price"]');
    const lineInput = tr.querySelector('input[name$="-line_total"]');

    if (!qtyInput || !priceInput || !lineInput) return;

    const qty = toNum(qtyInput.value);
    const price = toNum(priceInput.value);
    const total = (qty * price);
    lineInput.value = total.toFixed(2);
    return total;
  }

  function recalcAll() {
    const rows = document.querySelectorAll('#purchaseinvoiceitem_set-group table tr.form-row');
    let sum = 0;
    rows.forEach(function(tr) {
      const t = recalcRow(tr);
      if (typeof t === "number") sum += t;
    });

    // Update footer display (create if missing)
    let footer = document.getElementById("pi-inline-total-footer");
    if (!footer) {
      footer = document.createElement("div");
      footer.id = "pi-inline-total-footer";
      footer.style.textAlign = "right";
      footer.style.marginTop = "8px";
      const container = document.querySelector('#purchaseinvoiceitem_set-group');
      if (container) container.appendChild(footer);
    }
    footer.innerHTML = "<strong>Items Total: " + sum.toFixed(2) + "</strong>";

    // If you display parent fields on the page, mirror them for UX (server still recomputes):
    const totalField = document.getElementById("id_total_amount");
    if (totalField) totalField.value = sum.toFixed(2);

    // If you have discount/charges/tax inputs in parent form, you can mirror grand_total:
    const discount = toNum(document.getElementById("id_discount")?.value);
    const other = toNum(document.getElementById("id_other_charges")?.value);
    const tax = toNum(document.getElementById("id_tax_amount")?.value);
    const grand = sum - discount + other + tax;
    const grandField = document.getElementById("id_grand_total");
    if (grandField) grandField.value = grand.toFixed(2);
  }

  function bind() {
    document.body.addEventListener("input", function(e) {
      const target = e.target;
      if (!target || !target.name) return;

      // item-level fields
      if (/-quantity$/.test(target.name) || /-purchase_price$/.test(target.name)) {
        const tr = target.closest("tr");
        if (tr) recalcRow(tr);
        recalcAll();
      }

      // parent-level adjustments
      if (["id_discount","id_other_charges","id_tax_amount"].includes(target.id)) {
        recalcAll();
      }
    });

    // initial pass
    recalcAll();
  }

  if (document.readyState !== "loading") bind();
  else document.addEventListener("DOMContentLoaded", bind);
})();
