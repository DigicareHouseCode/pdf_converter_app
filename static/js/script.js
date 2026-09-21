/* Excel Automation — UI behaviour
   No external dependencies. Icons are inline SVG to match the server-rendered set. */

(function () {
    'use strict';

    var SVG = {
        upload: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
        file: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
        sparkle: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4L12 3z"/></svg>',
        alert: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
    };

    function esc(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    /* ---------- theme ---------- */

    var themeToggle = document.getElementById('themeToggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', function () {
            var root = document.documentElement;
            var current = root.getAttribute('data-theme');
            if (!current) {
                current = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
            }
            var next = current === 'dark' ? 'light' : 'dark';
            root.setAttribute('data-theme', next);
            try { localStorage.setItem('theme', next); } catch (e) { }
        });
    }

    /* ---------- FAQ ---------- */

    document.querySelectorAll('.faq-question').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var item = btn.closest('.faq-item');
            var open = item.classList.toggle('is-open');
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
    });

    /* ---------- review & correct table ---------- */

    var reviewRoot = document.getElementById('reviewRoot');
    if (reviewRoot) {
        (function () {
            var taskId = reviewRoot.dataset.taskId;
            var thead = document.getElementById('reviewHead');
            var tbody = document.querySelector('#reviewTable tbody');
            var wrap = document.getElementById('tableWrap');
            var status = document.getElementById('reviewStatus');
            var saveBtn = document.getElementById('saveBtn');
            var saveState = document.getElementById('saveState');
            var root = window.APP_ROOT || '';
            var dirty = false;
            var columns = [];      // data column names from the API
            var sheetType = 'transactions';

            // Column names that should be formatted/aligned as numbers
            var MONEY_COLS = ['debit', 'credit', 'balance', 'amount', 'courtesy_amount'];

            function isMoney(name) {
                return MONEY_COLS.indexOf(name.toLowerCase()) !== -1;
            }

            function money(v) {
                if (v === null || v === undefined || v === '') return '';
                var n = Number(v);
                return isNaN(n) ? String(v) : n.toFixed(2);
            }

            function markDirty(tr) {
                dirty = true;
                if (tr) tr.classList.add('row-dirty');
                saveBtn.disabled = false;
                saveState.textContent = 'Unsaved changes';
                saveState.removeAttribute('data-state');
            }

            function cell(value, cls, tr) {
                var td = document.createElement('td');
                var input = document.createElement('input');
                input.className = 'cell' + (cls ? ' ' + cls : '');
                input.value = value === null || value === undefined ? '' : value;
                input.addEventListener('input', function () { markDirty(tr); });
                td.appendChild(input);
                return td;
            }

            function buildHeader() {
                thead.textContent = '';
                // Row number
                var th0 = document.createElement('th');
                th0.className = 'col-n';
                th0.textContent = '#';
                thead.appendChild(th0);
                // Data columns
                columns.forEach(function (name) {
                    var th = document.createElement('th');
                    if (isMoney(name)) th.className = 'col-money';
                    th.textContent = name;
                    thead.appendChild(th);
                });
                // Review column (skip for checks)
                if (sheetType !== 'checks') {
                    var thR = document.createElement('th');
                    thR.textContent = 'Review';
                    thead.appendChild(thR);
                }
            }

            function render(rows) {
                tbody.textContent = '';
                rows.forEach(function (row, i) {
                    var tr = document.createElement('tr');
                    if (row.Review) tr.className = 'is-flagged';

                    var n = document.createElement('td');
                    n.className = 'cell--index';
                    n.textContent = i + 1;
                    tr.appendChild(n);

                    columns.forEach(function (colName) {
                        var val = row[colName];
                        var cls = isMoney(colName) ? 'cell--num' : '';
                        if (isMoney(colName)) val = money(val);
                        tr.appendChild(cell(val, cls, tr));
                    });

                    if (sheetType !== 'checks') {
                        var note = document.createElement('td');
                        note.className = 'cell--note';
                        note.textContent = row.Review || '';
                        tr.appendChild(note);
                    }

                    tbody.appendChild(tr);
                });
            }

            function collect() {
                return Array.prototype.map.call(tbody.querySelectorAll('tr'), function (tr) {
                    var inputs = tr.querySelectorAll('input.cell');
                    var obj = {};
                    columns.forEach(function (colName, idx) {
                        obj[colName] = inputs[idx] ? inputs[idx].value : '';
                    });
                    return obj;
                });
            }

            fetch(root + '/api/rows/' + taskId)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data.rows || !data.rows.length) {
                        status.textContent = 'No rows to show for this conversion.';
                        return;
                    }
                    columns = data.columns || [];
                    sheetType = data.sheet_type || 'transactions';
                    buildHeader();
                    render(data.rows);
                    status.hidden = true;
                    wrap.hidden = false;
                })
                .catch(function () {
                    status.textContent = 'Could not load the extracted data. The download still works.';
                });

            saveBtn.addEventListener('click', function () {
                saveBtn.disabled = true;
                saveState.textContent = 'Saving…';
                saveState.removeAttribute('data-state');

                fetch(root + '/api/rows/' + taskId, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
                    body: JSON.stringify({ rows: collect(), sheet_type: sheetType })
                })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.error) throw new Error(data.error);
                        dirty = false;
                        saveState.textContent = data.flagged
                            ? 'Saved — ' + data.flagged + ' row(s) still flagged'
                            : 'Saved — all checks passed';
                        saveState.setAttribute('data-state', 'saved');
                        tbody.querySelectorAll('.row-dirty').forEach(function (tr) {
                            tr.classList.remove('row-dirty');
                        });
                    })
                    .catch(function () {
                        saveState.textContent = 'Could not save. Try again.';
                        saveState.setAttribute('data-state', 'error');
                        saveBtn.disabled = false;
                    });
            });

            window.addEventListener('beforeunload', function (e) {
                if (dirty) { e.preventDefault(); e.returnValue = ''; }
            });
        })();
    }

    /* ---------- upload form ---------- */

    var fileInput = document.getElementById('fileInput');
    var fileLabel = document.querySelector('.file-input-label');
    var convertBtn = document.getElementById('convertBtn');
    var uploadForm = document.getElementById('uploadForm');
    var processingPanel = document.getElementById('processingMessage');
    var promptSection = document.getElementById('customPromptSection');
    var promptField = document.getElementById('customPrompt');
    var charCount = document.getElementById('charCount');

    if (!uploadForm) return;

    var folderInput = document.getElementById('folderInput');
    var folderBtn = document.getElementById('folderBtn');
    var uploadExtra = document.getElementById('uploadExtra');
    var fileList = document.getElementById('fileList');
    var maxChecks = parseInt(uploadForm.dataset.maxChecks || '300', 10);

    // Files chosen via the folder picker, kept aside because a file input's
    // FileList cannot be merged with another one.
    var folderFiles = [];

    // Every mode reads PDFs and images; checks mode additionally takes a batch.
    // 'image/*' rather than a list of extensions: Windows saves JPEGs as .jfif
    // and an explicit list quietly greys those out in the file picker. The
    // server is what actually decides, and it names any format it cannot read.
    var UPLOAD_ACCEPT = '.pdf,image/*';

    function isChecksMode() {
        var selected = document.querySelector('input[name="conversion_type"]:checked');
        return !!selected && selected.value === 'checks';
    }

    function setLabel(name, sizeText, selected) {
        fileLabel.innerHTML =
            (selected ? SVG.file : SVG.upload) +
            '<span class="file-text">' + esc(name) + '</span>' +
            '<span class="file-size">' + esc(sizeText) + '</span>';
        fileLabel.classList.toggle('file-selected', selected);
    }

    function resetLabel() {
        if (isChecksMode()) {
            setLabel('Choose check images or a PDF', 'PNG, JPG, GIF, TIFF or PDF — several at once', false);
        } else {
            setLabel('Choose a PDF or image, or drag it here',
                'PDF or image — PNG, JPG, JFIF, GIF, TIFF, BMP, WEBP', false);
        }
    }

    function chosenFiles() {
        var picked = fileInput.files ? Array.prototype.slice.call(fileInput.files) : [];
        return isChecksMode() ? picked.concat(folderFiles) : picked.slice(0, 1);
    }

    function totalMB(files) {
        var bytes = files.reduce(function (sum, f) { return sum + f.size; }, 0);
        return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    }

    function renderFileList(files) {
        if (!fileList) return;
        if (!isChecksMode() || files.length < 2) {
            fileList.hidden = true;
            fileList.textContent = '';
            return;
        }
        fileList.textContent = '';
        files.slice(0, 12).forEach(function (f) {
            var row = document.createElement('span');
            row.className = 'file-chip';
            row.textContent = f.webkitRelativePath || f.name;
            fileList.appendChild(row);
        });
        if (files.length > 12) {
            var more = document.createElement('span');
            more.className = 'file-chip file-chip--more';
            more.textContent = '+' + (files.length - 12) + ' more';
            fileList.appendChild(more);
        }
        fileList.hidden = false;
    }

    function syncSelection() {
        var files = chosenFiles();

        if (!files.length) {
            resetLabel();
            renderFileList([]);
            return;
        }

        if (isChecksMode() && files.length > 1) {
            setLabel(files.length + ' files selected', totalMB(files), true);
        } else {
            setLabel(files[0].name, (files[0].size / 1024 / 1024).toFixed(1) + ' MB', true);
        }

        renderFileList(files);
        removeError();
    }

    if (fileInput) {
        fileInput.addEventListener('change', function () {
            folderFiles = [];          // a fresh pick replaces any folder choice
            syncSelection();
        });
    }

    if (folderBtn && folderInput) {
        folderBtn.addEventListener('click', function () { folderInput.click(); });
        folderInput.addEventListener('change', function () {
            folderFiles = Array.prototype.slice.call(this.files).filter(function (f) {
                return /\.(pdf|png|jpe?g|jpe|jfif|gif|tiff?|webp|bmp|avif)$/i.test(f.name);
            });
            if (fileInput.value) fileInput.value = '';   // folder choice wins
            syncSelection();
        });
    }

    /* drag and drop */
    if (fileLabel) {
        ['dragenter', 'dragover'].forEach(function (evt) {
            fileLabel.addEventListener(evt, function (e) {
                e.preventDefault();
                fileLabel.classList.add('is-dragging');
            });
        });
        ['dragleave', 'drop'].forEach(function (evt) {
            fileLabel.addEventListener(evt, function (e) {
                e.preventDefault();
                if (evt === 'dragleave' && fileLabel.contains(e.relatedTarget)) return;
                fileLabel.classList.remove('is-dragging');
            });
        });
        fileLabel.addEventListener('drop', function (e) {
            if (e.dataTransfer.files.length) {
                fileInput.files = e.dataTransfer.files;
                fileInput.dispatchEvent(new Event('change'));
            }
        });
    }

    /* mode switching */
    function syncMode() {
        var selected = document.querySelector('input[name="conversion_type"]:checked');
        var mode = selected ? selected.value : '';
        var isCustom = mode === 'custom';
        var isChecks = mode === 'checks';

        if (promptSection) {
            var label = document.getElementById('promptLabel');
            if (label) {
                label.textContent = 'Extraction instructions ' + (isCustom ? '(Required)' : '(Optional)');
            }
            if (promptField) {
                promptField.required = isCustom;
            }
        }
        if (uploadExtra) uploadExtra.style.display = isChecks ? 'block' : 'none';

        // Only checks mode takes a batch.
        fileInput.multiple = isChecks;
        fileInput.accept = UPLOAD_ACCEPT;

        var dt = document.getElementById('dropzoneText');
        var ds = document.getElementById('dropzoneSubtext');
        if (dt) {
            dt.textContent = isChecks ? 'Choose check images or a PDF'
                : 'Choose a PDF or image, or drag it here';
        }
        if (ds) {
            ds.textContent = isChecks ? 'PNG, JPG, GIF, TIFF or PDF — several at once'
                : 'PDF or image — PNG, JPG, JFIF, GIF, TIFF, BMP, WEBP';
        }

        if (!isChecks && folderFiles.length) {
            folderFiles = [];
            if (folderInput) folderInput.value = '';
        }

        if (convertBtn && !convertBtn.disabled) {
            var label = isCustom ? 'Convert with my rules'
                : isChecks ? 'Extract checks to Excel'
                    : 'Extract to Excel';
            convertBtn.innerHTML = SVG.sparkle + ' ' + label;
        }

        syncSelection();
    }
    document.querySelectorAll('input[name="conversion_type"]').forEach(function (r) {
        r.addEventListener('change', syncMode);
    });
    syncMode();

    if (promptField && charCount) {
        var updateCount = function () { charCount.textContent = promptField.value.length; };
        promptField.addEventListener('input', updateCount);
        updateCount();
    }

    document.querySelectorAll('.example-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!promptField) return;
            promptField.value = this.dataset.prompt;
            promptField.dispatchEvent(new Event('input'));
            promptField.focus();
        });
    });

    /* ---------- submit + progress ---------- */

    var progressBar = document.getElementById('mainProgressBar');
    var progressText = document.getElementById('mainProgressText');
    var subStatus = document.getElementById('mainSubStatusText');

    uploadForm.addEventListener('submit', function (e) {
        e.preventDefault();

        var selected = document.querySelector('input[name="conversion_type"]:checked');
        var files = chosenFiles();

        if (!selected) { return showError('Pick a conversion mode.'); }
        if (!files.length) {
            return showError(isChecksMode()
                ? 'Choose the check images or PDF you want to read.'
                : 'Choose a PDF or image file to convert.');
        }
        if (isChecksMode() && files.length > maxChecks) {
            return showError('That is ' + files.length + ' files. Upload at most ' + maxChecks + ' at a time.');
        }
        if (selected.value === 'custom' && !promptField.value.trim()) {
            showError('Describe the columns you want extracted.');
            promptField.focus();
            return;
        }

        // Build the payload by hand: files may come from either input, and the
        // relative path of each one has to travel alongside it.
        var payload = new FormData();
        payload.append('csrf_token', uploadForm.querySelector('[name="csrf_token"]').value);
        payload.append('conversion_type', selected.value);
        if (promptField) payload.append('custom_prompt', promptField.value);
        files.forEach(function (f) {
            payload.append('file', f, f.name);
            payload.append('relative_paths', f.webkitRelativePath || f.name);
        });

        processingPanel.classList.add('is-active');
        convertBtn.disabled = true;
        convertBtn.textContent = 'Converting…';

        fetch(this.action, {
            method: 'POST',
            body: payload,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
            .then(function (res) {
                if (res.redirected) { window.location.href = res.url; return null; }
                var ct = res.headers.get('content-type') || '';
                if (ct.indexOf('application/json') === -1) {
                    throw new Error('Server returned ' + res.status);
                }
                return res.json();
            })
            .then(function (data) {
                if (!data) return;
                if (data.task_id) { poll(data.task_id); }
                else { showError(data.error || 'The upload did not start. Try again.'); resetUI(); }
            })
            .catch(function () {
                showError('The upload failed. Check your connection and try again.');
                resetUI();
            });
    });

    function poll(taskId) {
        var root = window.APP_ROOT || '';
        var timer = setInterval(function () {
            fetch(root + '/progress/' + taskId)
                .then(function (res) {
                    var ct = res.headers.get('content-type') || '';
                    if (ct.indexOf('application/json') === -1) throw new Error('bad response');
                    return res.json();
                })
                .then(function (data) {
                    if (!data) return;
                    var pct = Math.round(data.progress || 0);
                    if (progressBar) progressBar.style.width = pct + '%';
                    if (progressText) progressText.textContent = pct + '%';
                    if (subStatus && data.message) subStatus.textContent = data.message;

                    if (data.status === 'completed') {
                        clearInterval(timer);
                        if (subStatus) subStatus.textContent = 'Done. Taking you to your file…';
                        setTimeout(function () { window.location.href = root + '/result/' + taskId; }, 600);
                    } else if (data.status === 'failed') {
                        clearInterval(timer);
                        showError(data.message || 'The conversion failed.');
                        resetUI();
                    }
                })
                .catch(function () { /* transient — keep polling */ });
        }, 1000);
    }

    function resetUI() {
        if (processingPanel) processingPanel.classList.remove('is-active');
        if (convertBtn) { convertBtn.disabled = false; syncMode(); }
    }

    function showError(message) {
        removeError();
        var div = document.createElement('div');
        div.className = 'error-message';
        div.setAttribute('role', 'alert');
        div.innerHTML = SVG.alert +
            '<span class="error-details"><strong>Check this first</strong><span>' + esc(message) + '</span></span>';
        fileLabel.insertAdjacentElement('afterend', div);
        setTimeout(removeError, 6000);
    }

    function removeError() {
        var existing = uploadForm.querySelector('.error-message');
        if (existing) existing.remove();
    }
})();
