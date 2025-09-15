(function () {
  var $ = (window.django && window.django.jQuery) ? window.django.jQuery : window.jQuery;
  if (!$) return;

  function isSelect2($el){ return $el && $el.hasClass('admin-autocomplete'); }
  function setSelect2($el, id, text){
    $el.find('option').remove();
    if (id !== null && id !== undefined && id !== "") {
      var opt = new Option(text, String(id), true, true);
      $el.append(opt).val(String(id)).trigger('change');
    } else {
      $el.val(null).trigger('change');
    }
  }
  function setVal(sel, val){ var $f = $(sel); if ($f.length) $f.val(val); }

  function inlinePrefix(){ return $('#id_items-TOTAL_FORMS').length ? 'items' : null; }

  // remove current rows via admin's own handlers so delete works
  function clearInlineRows(prefix){
    $('tr.dynamic-' + prefix).each(function(){
      var $remove = $(this).find('a.inline-deletelink');
      if ($remove.length) { $remove.trigger('click'); } else { $(this).remove(); }
    });
    // mark existing persisted rows for DELETE
    $('input[id^="id_' + prefix + '-"][id$="-DELETE"]').each(function(){
      this.checked = true;
      $(this).closest('tr').hide();
    });
  }

  function addInlineRow(prefix, data){
    var $addBtn = $('tr.add-row a.addlink, .add-row a').first();
    if (!$addBtn.length) { console.warn('Add-row link not found'); return; }
    $addBtn.trigger('click');

    var idx = parseInt($('#id_' + prefix + '-TOTAL_FORMS').val() || '1', 10) - 1;
    var base = '#id_' + prefix + '-' + idx + '-';

    // Set grn_item (NEW) + others
    setVal(base + 'grn_item', data.grn_item_id);
    // product (raw id or simple select)
    var $prod = $(base + 'product');
    if ($prod.length) {
      if ($prod.find('option[value="' + data.product_id + '"]').length === 0) {
        $prod.append(new Option(data.product_label || ('#'+data.product_id), data.product_id, true, true));
      }
      $prod.val(String(data.product_id));
    } else {
      setVal(base + 'product', data.product_id);
    }

    setVal(base + 'batch_number',   data.batch_number || '');
    setVal(base + 'expiry_date',    data.expiry_date || '');
    setVal(base + 'purchase_price', data.purchase_price || '0');
    setVal(base + 'sale_price',     data.sale_price || '0');

    var qty = parseInt(data.default_qty || 0, 10);
    setVal(base + 'quantity', qty);

    var price = parseFloat(data.purchase_price || '0');
    var amt = (isFinite(price) ? price * qty : 0);
    setVal(base + 'amount', amt.toFixed(2));
  }

  function populateFromInvoice(resp){
    var $sup = $('#id_supplier');
    var $wh  = $('#id_warehouse');
    if (isSelect2($sup)) setSelect2($sup, resp.supplier.id, resp.supplier.text); else $sup.val(String(resp.supplier.id));
    if (isSelect2($wh))  setSelect2($wh,  resp.warehouse.id, resp.warehouse.text); else $wh.val(String(resp.warehouse.id));

    setVal('#id_date', resp.invoice.date || '');

    var prefix = inlinePrefix();
    if (!prefix) return;

    var hasExisting = $('tr.dynamic-' + prefix).length > 0 ||
                      parseInt($('#id_' + prefix + '-TOTAL_FORMS').val() || '0', 10) > 0;
    // if (hasExisting) {
    //   if (!confirm('Replace current return items with GRN lines from the selected invoice?')) return;
    // }

    clearInlineRows(prefix);
    (resp.items || []).forEach(function(it){ addInlineRow(prefix, it); });
  }

  function buildJsonUrl(invoiceId){
    if (window.PURCHASE_RETURN_INVOICE_GRN_JSON_BASE) {
      return window.PURCHASE_RETURN_INVOICE_GRN_JSON_BASE.replace(/\/$/, '') + '/' + invoiceId + '/';
    }
    // fallback
    return '/admin/purchase/purchasereturn/invoice-grn-data/' + invoiceId + '/';
  }

  function recalcAmountForInput($input){
    var id = $input.attr('id'); // e.g. id_items-3-quantity or id_items-3-purchase_price
    var m = id && id.match(/^id_(items)-(\d+)-(quantity|purchase_price)$/);
    if (!m) return;
    var prefix = m[1], idx = m[2];
    var base = '#id_' + prefix + '-' + idx + '-';
    var qty   = parseFloat($(base + 'quantity').val() || '0');
    var price = parseFloat($(base + 'purchase_price').val() || '0');
    var amt = (isFinite(qty) && isFinite(price)) ? (qty * price) : 0;
    $(base + 'amount').val(amt.toFixed(2));
  }

  $(function(){
    var $inv = $('#id_invoice');
    if ($inv.length){
      $inv.on('change', function(){
        var val = $(this).val();
        if (!val) return;
        $.getJSON(buildJsonUrl(val))
          .done(populateFromInvoice)
          .fail(function(xhr){
            console.error('GRN data load failed:', xhr.status, xhr.responseText);
            alert('Failed to load GRN lines for the selected invoice.');
          });
      });
    }

    // keep amount in sync if user edits qty/price
    $(document).on('input', 'input[id^="id_items-"][id$="-quantity"], input[id^="id_items-"][id$="-purchase_price"]', function(){
      recalcAmountForInput($(this));
    });
  });
})();
