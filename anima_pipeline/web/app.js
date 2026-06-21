document.addEventListener('DOMContentLoaded', () => {
    // Initialize Lucide Icons
    lucide.createIcons();

    // Element references
    const btnGenerate = document.getElementById('btn-generate');
    const inputPrompt = document.getElementById('input-prompt');
    const inputTags = document.getElementById('input-tags');
    const suggestChips = document.querySelectorAll('.suggest-chip');
    
    // History & State variables
    let history = [];
    let currentResult = null;
    
    // Output fields
    const outputEnglish = document.getElementById('output-english');
    const outputAnima = document.getElementById('output-anima');
    const outputNegative = document.getElementById('output-negative');
    
    // Status items
    const statusGemma = document.getElementById('status-gemma');
    const statusDict = document.getElementById('status-dict');
    
    // Settings elements
    const settingsTrigger = document.getElementById('settings-trigger');
    const settingsContent = document.getElementById('settings-content');
    const settingsAccordion = settingsTrigger.closest('.settings-accordion');
    
    const settingTemp = document.getElementById('setting-temp');
    const valTemp = document.getElementById('val-temp');
    const settingMaxTokens = document.getElementById('setting-max-tokens');
    const settingFuzzy = document.getElementById('setting-fuzzy');
    const valFuzzy = document.getElementById('val-fuzzy');
    const settingTranslate = document.getElementById('setting-translate');

    // System Alert
    const systemAlert = document.getElementById('system-alert');
    const alertTitle = document.getElementById('alert-title');
    const alertMessage = document.getElementById('alert-message');

    // Validation Issues Card
    const cardIssues = document.getElementById('card-issues');
    const issuesList = document.getElementById('issues-list');

    // History references
    const btnSaveHistory = document.getElementById('btn-save-history');
    const historyList = document.getElementById('history-list');
    const btnExport = document.getElementById('btn-export');
    const btnImport = document.getElementById('btn-import');
    const csvImportFile = document.getElementById('csv-import-file');

    // Toggle Advanced Settings Accordion
    settingsTrigger.addEventListener('click', () => {
        settingsAccordion.classList.toggle('open');
    });

    // Sync Slider value displays
    settingTemp.addEventListener('input', (e) => {
        valTemp.textContent = e.target.value;
    });

    settingFuzzy.addEventListener('input', (e) => {
        valFuzzy.textContent = e.target.value;
    });

    // Preset chips click
    suggestChips.forEach(chip => {
        chip.addEventListener('click', () => {
            inputPrompt.value = chip.textContent;
            inputPrompt.focus();
        });
    });

    // Check backend status on load
    async function checkStatus() {
        try {
            const res = await fetch('/api/status');
            if (!res.ok) throw new Error('API returns error');
            const data = await res.json();

            // Update Gemma status
            const dotGemma = statusGemma.querySelector('.status-dot');
            const textGemma = statusGemma.querySelector('.status-text');
            if (data.gemma_online) {
                dotGemma.className = 'status-dot online';
                textGemma.textContent = 'Gemma: Online';
            } else {
                dotGemma.className = 'status-dot offline';
                textGemma.textContent = 'Gemma: Offline';
                showSystemAlert(
                    'Gemma サーバーに接続できません',
                    `設定された URL: ${data.gemma_url} に接続できません。別のターミナルで llama-server を起動しているか確認してください。<br><br><code>~/llama.cpp/build/bin/llama-server -m ~/llama.cpp/models/gemma-4-26B-A4B-it-Q4_K_M.gguf --port 8088 -c 8192 -ngl 99 -ot "\\.ffn_(up|down|gate)_exps\\.=CPU" -fa on --jinja --reasoning-budget 0</code>`
                );
            }

            // Update Dictionary status
            const dotDict = statusDict.querySelector('.status-dot');
            const textDict = statusDict.querySelector('.status-text');
            if (data.dictionary_exists) {
                dotDict.className = 'status-dot online';
                textDict.textContent = 'Dict: Loaded';
            } else {
                dotDict.className = 'status-dot offline';
                textDict.textContent = 'Dict: Missing';
                showSystemAlert(
                    '辞書ファイルが見つかりません',
                    `辞書が未作成です。先に一般タグ辞書を作成してください。<br>ターミナル（anima_pipeline/ の1つ上の階層など）で以下を実行してください。<br><br><code>python build_anima_dictionary.py --danbooru anima_pipeline/data/raw/danbooru.csv --gelbooru anima_pipeline/data/raw/gelbooru.csv --keep-categories 0 --min-count 10 --out-dir anima_pipeline/data/dict</code>`
                );
            }
        } catch (e) {
            console.error(e);
            // If backend is entirely offline
            const dotGemma = statusGemma.querySelector('.status-dot');
            const textGemma = statusGemma.querySelector('.status-text');
            dotGemma.className = 'status-dot offline';
            textGemma.textContent = 'API Server: Offline';
            
            const dotDict = statusDict.querySelector('.status-dot');
            const textDict = statusDict.querySelector('.status-text');
            dotDict.className = 'status-dot offline';
            textDict.textContent = 'Dict: Unknown';

            showSystemAlert(
                'Web サーバーに接続できません',
                'FastAPI バックエンドサーバーが起動していない可能性があります。<code>python app_web.py</code> を実行してサーバーを起動してください。'
            );
        }
    }

    // Helper to display alert
    function showSystemAlert(title, htmlMessage, type = 'error') {
        systemAlert.classList.remove('hidden');
        if (type === 'info') {
            systemAlert.className = 'alert-box info';
        } else {
            systemAlert.className = 'alert-box';
        }
        alertTitle.textContent = title;
        alertMessage.innerHTML = htmlMessage;
    }

    function hideSystemAlert() {
        systemAlert.classList.add('hidden');
    }

    // Copy to clipboard functionality
    document.querySelectorAll('.btn-copy').forEach(button => {
        button.addEventListener('click', async () => {
            const targetId = button.getAttribute('data-target');
            const codeEl = document.getElementById(targetId);
            
            if (codeEl.classList.contains('empty')) {
                return;
            }

            const textToCopy = codeEl.textContent;
            
            try {
                await navigator.clipboard.writeText(textToCopy);
                
                // Visual feedback
                button.classList.add('copied');
                const originalHtml = button.innerHTML;
                button.innerHTML = '<i data-lucide="check"></i> Copied!';
                lucide.createIcons(); // refresh icons inside button
                
                setTimeout(() => {
                    button.classList.remove('copied');
                    button.innerHTML = originalHtml;
                    lucide.createIcons();
                }, 1500);
            } catch (err) {
                console.error('Failed to copy text: ', err);
                alert('コピーに失敗しました。お使いのブラウザがクリップボードAPIに対応していない可能性があります。');
            }
        });
    });

    // Generate action
    btnGenerate.addEventListener('click', async () => {
        const jaPrompt = inputPrompt.value.trim();
        if (!jaPrompt) {
            alert('日本語プロンプトを入力してください。');
            inputPrompt.focus();
            return;
        }

        // Parse extra tags
        const tagsString = inputTags.value.trim();
        const extraTags = tagsString ? tagsString.split(',').map(t => t.trim()).filter(Boolean) : [];

        // Advanced configurations
        const temperature = parseFloat(settingTemp.value);
        const maxTokens = parseInt(settingMaxTokens.value, 10);
        const fuzzyCutoff = parseInt(settingFuzzy.value, 10);
        const translateFirst = settingTranslate.checked;

        // UI update: Loading state
        btnGenerate.disabled = true;
        btnGenerate.querySelector('.btn-text').classList.add('hidden');
        btnGenerate.querySelector('.spinner').classList.remove('hidden');
        hideSystemAlert();

        try {
            const response = await fetch('/api/generate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    prompt: jaPrompt,
                    extra_tags: extraTags,
                    temperature,
                    max_tokens: maxTokens,
                    fuzzy_cutoff: fuzzyCutoff,
                    translate_first: translateFirst
                })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'プロンプト生成に失敗しました。');
            }

            const data = await response.json();

            // Populate output fields
            outputEnglish.textContent = data.english || '';
            outputEnglish.classList.remove('empty');

            outputAnima.textContent = data.prompt || '';
            outputAnima.classList.remove('empty');

            outputNegative.textContent = data.negative || '';
            outputNegative.classList.remove('empty');

            // Render issues (validation results)
            renderIssues(data.issues || []);

            // Save current result for history saving
            currentResult = {
                jaPrompt: jaPrompt,
                extraTags: tagsString,
                english: data.english || '',
                anima: data.prompt || '',
                negative: data.negative || '',
                issues: data.issues || [],
                settings: {
                    temperature,
                    maxTokens,
                    fuzzyCutoff,
                    translateFirst
                }
            };
            btnSaveHistory.disabled = false;

        } catch (error) {
            console.error(error);
            showSystemAlert('エラーが発生しました', error.message);
            
            // Clear outputs
            outputEnglish.textContent = 'エラーが発生しました。詳細は上部の警告を確認してください。';
            outputEnglish.classList.add('empty');
            outputAnima.textContent = 'エラーが発生しました。';
            outputAnima.classList.add('empty');
            outputNegative.textContent = 'エラーが発生しました。';
            outputNegative.classList.add('empty');
            cardIssues.classList.add('hidden');
        } finally {
            // UI update: Reset state
            btnGenerate.disabled = false;
            btnGenerate.querySelector('.btn-text').classList.remove('hidden');
            btnGenerate.querySelector('.spinner').classList.add('hidden');
        }
    });

    // Render validation results
    function renderIssues(issues) {
        cardIssues.classList.remove('hidden');
        issuesList.innerHTML = '';
        
        const headerIconSuccess = cardIssues.querySelector('.issue-icon-success');
        const headerIconWarning = cardIssues.querySelector('.issue-icon-warning');
        
        if (issues.length === 0) {
            // Success State
            headerIconSuccess.classList.remove('hidden');
            headerIconWarning.classList.add('hidden');
            
            issuesList.innerHTML = `
                <div class="no-issues">
                    <i data-lucide="check-circle-2"></i>
                    バリデーションチェックをクリアしました！問題は見つかりませんでした。
                </div>
            `;
        } else {
            // Warning State
            headerIconSuccess.classList.add('hidden');
            headerIconWarning.classList.remove('hidden');
            
            issues.forEach(issue => {
                const item = document.createElement('div');
                item.className = 'issue-item';
                item.innerHTML = `
                    <i data-lucide="alert-octagon"></i>
                    <span>${escapeHtml(issue)}</span>
                `;
                issuesList.appendChild(item);
            });
        }
        lucide.createIcons();
    }

    // Helper to escape HTML characters
    function escapeHtml(str) {
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // Check status initially
    checkStatus();

    // --- History & CSV Functionality ---

    // Load history from localStorage
    function loadHistoryFromStorage() {
        const stored = localStorage.getItem('anima_prompt_history');
        if (stored) {
            try {
                history = JSON.parse(stored);
            } catch (e) {
                console.error('Failed to parse history from localStorage', e);
                history = [];
            }
        } else {
            history = [];
        }
        renderHistory();
    }

    // Render history items to UI
    function renderHistory() {
        historyList.innerHTML = '';
        
        if (history.length === 0) {
            historyList.innerHTML = `
                <div class="no-history">
                    <i data-lucide="folder-open"></i>
                    <span>保存された履歴はありません。</span>
                </div>
            `;
            lucide.createIcons();
            return;
        }
        
        // Render in reverse order (newest first)
        [...history].reverse().forEach((item, index) => {
            const originalIndex = history.length - 1 - index;
            
            const historyItem = document.createElement('div');
            historyItem.className = 'history-item';
            
            const shortPrompt = item.jaPrompt.length > 30 
                ? item.jaPrompt.substring(0, 30) + '...' 
                : item.jaPrompt;
                
            historyItem.innerHTML = `
                <div class="history-item-details">
                    <span class="history-item-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
                    <span class="history-item-prompt" title="${escapeHtml(item.jaPrompt)}">${escapeHtml(shortPrompt)}</span>
                    <span class="history-item-meta">${item.datetime}</span>
                </div>
                <div class="history-item-actions">
                    <button class="btn btn-copy btn-history-load" data-index="${originalIndex}" title="ロード">
                        <i data-lucide="folder-open"></i>
                    </button>
                    <button class="btn btn-copy btn-history-delete" data-index="${originalIndex}" title="削除">
                        <i data-lucide="trash-2"></i>
                    </button>
                </div>
            `;
            
            historyItem.querySelector('.btn-history-load').addEventListener('click', () => {
                loadHistoryItem(originalIndex);
            });
            
            historyItem.querySelector('.btn-history-delete').addEventListener('click', () => {
                deleteHistoryItem(originalIndex);
            });
            
            historyList.appendChild(historyItem);
        });
        
        lucide.createIcons();
    }

    // Load history item into input and outputs
    function loadHistoryItem(index) {
        const item = history[index];
        if (!item) return;
        
        // Restore input fields
        inputPrompt.value = item.jaPrompt;
        inputPrompt.focus();
        inputTags.value = item.extraTags || '';
        
        // Restore settings
        if (item.settings) {
            if (item.settings.temperature !== undefined) {
                settingTemp.value = item.settings.temperature;
                valTemp.textContent = item.settings.temperature;
            }
            if (item.settings.maxTokens !== undefined) {
                settingMaxTokens.value = item.settings.maxTokens;
            }
            if (item.settings.fuzzyCutoff !== undefined) {
                settingFuzzy.value = item.settings.fuzzyCutoff;
                valFuzzy.textContent = item.settings.fuzzyCutoff;
            }
            if (item.settings.translateFirst !== undefined) {
                settingTranslate.checked = item.settings.translateFirst;
            }
        }
        
        // Restore outputs
        outputEnglish.textContent = item.english || '';
        outputEnglish.classList.remove('empty');

        outputAnima.textContent = item.anima || '';
        outputAnima.classList.remove('empty');

        outputNegative.textContent = item.negative || '';
        outputNegative.classList.remove('empty');
        
        // Restore validation results
        renderIssues(item.issues || []);
        
        // Update current state for saving again
        currentResult = {
            jaPrompt: item.jaPrompt,
            extraTags: item.extraTags || '',
            english: item.english || '',
            anima: item.anima || '',
            negative: item.negative || '',
            issues: item.issues || [],
            settings: item.settings ? { ...item.settings } : null
        };
        btnSaveHistory.disabled = false;
        
        showTemporaryNotice(`履歴「${item.name}」をロードしました。`);
    }

    // Delete history item
    function deleteHistoryItem(index) {
        const item = history[index];
        if (!item) return;
        
        if (confirm(`履歴「${item.name}」を削除してもよろしいですか？`)) {
            history.splice(index, 1);
            localStorage.setItem('anima_prompt_history', JSON.stringify(history));
            renderHistory();
        }
    }

    // Save current result
    btnSaveHistory.addEventListener('click', () => {
        if (!currentResult) return;
        
        const overlay = document.createElement('div');
        overlay.className = 'save-modal-overlay';
        
        const date = new Date();
        const defaultName = currentResult.jaPrompt.substring(0, 15).trim() || '無題のプロンプト';
        
        overlay.innerHTML = `
            <div class="save-modal">
                <h3>結果を保存</h3>
                <div class="input-group">
                    <label for="modal-save-name">保存名・メモ</label>
                    <input type="text" id="modal-save-name" placeholder="例: キャラ設定A" value="${escapeHtml(defaultName)}">
                </div>
                <div class="save-modal-buttons">
                    <button class="btn btn-copy" id="btn-modal-cancel">キャンセル</button>
                    <button class="btn btn-primary" id="btn-modal-save">保存</button>
                </div>
            </div>
        `;
        
        document.body.appendChild(overlay);
        
        const inputName = overlay.querySelector('#modal-save-name');
        inputName.focus();
        inputName.select();
        
        overlay.querySelector('#btn-modal-cancel').addEventListener('click', () => {
            overlay.remove();
        });
        
        const doSave = () => {
            const name = inputName.value.trim() || defaultName;
            
            const yyyy = date.getFullYear();
            const mm = String(date.getMonth() + 1).padStart(2, '0');
            const dd = String(date.getDate()).padStart(2, '0');
            const hh = String(date.getHours()).padStart(2, '0');
            const min = String(date.getMinutes()).padStart(2, '0');
            const ss = String(date.getSeconds()).padStart(2, '0');
            const datetimeStr = `${yyyy}-${mm}-${dd} ${hh}:${min}:${ss}`;
            
            const newHistoryItem = {
                id: Date.now().toString(),
                name: name,
                datetime: datetimeStr,
                jaPrompt: currentResult.jaPrompt,
                extraTags: currentResult.extraTags,
                english: currentResult.english,
                anima: currentResult.anima,
                negative: currentResult.negative,
                issues: currentResult.issues,
                settings: currentResult.settings ? { ...currentResult.settings } : null
            };
            
            history.push(newHistoryItem);
            localStorage.setItem('anima_prompt_history', JSON.stringify(history));
            
            overlay.remove();
            renderHistory();
            showTemporaryNotice(`履歴に「${name}」を保存しました。`);
        };
        
        overlay.querySelector('#btn-modal-save').addEventListener('click', doSave);
        
        inputName.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                doSave();
            }
        });
    });

    // CSV helpers
    function arrayToCSV(data) {
        const headers = ['Name', 'Datetime', 'JaPrompt', 'ExtraTags', 'English', 'Anima', 'Negative', 'Temperature', 'MaxTokens', 'FuzzyCutoff', 'TranslateFirst'];
        const rows = [headers];
        
        data.forEach(item => {
            rows.push([
                item.name,
                item.datetime,
                item.jaPrompt,
                item.extraTags,
                item.english,
                item.anima,
                item.negative,
                item.settings?.temperature ?? '',
                item.settings?.maxTokens ?? '',
                item.settings?.fuzzyCutoff ?? '',
                item.settings?.translateFirst ?? ''
            ]);
        });
        
        return rows.map(row => 
            row.map(val => {
                const str = String(val ?? '');
                const escaped = str.replace(/"/g, '""');
                if (escaped.includes('"') || escaped.includes(',') || escaped.includes('\n') || escaped.includes('\r')) {
                    return `"${escaped}"`;
                }
                return escaped;
            }).join(',')
        ).join('\r\n');
    }

    function parseCSV(text) {
        const lines = [];
        let row = [""];
        let inQuotes = false;

        for (let i = 0; i < text.length; i++) {
            const c = text[i];
            const next = text[i+1];

            if (inQuotes) {
                if (c === '"') {
                    if (next === '"') {
                        row[row.length - 1] += '"';
                        i++;
                    } else {
                        inQuotes = false;
                    }
                } else {
                    row[row.length - 1] += c;
                }
            } else {
                if (c === '"') {
                    inQuotes = true;
                } else if (c === ',') {
                    row.push("");
                } else if (c === '\r' || c === '\n') {
                    if (c === '\r' && next === '\n') {
                        i++;
                    }
                    lines.push(row);
                    row = [""];
                } else {
                    row[row.length - 1] += c;
                }
            }
        }
        if (row.length > 1 || row[0] !== "") {
            lines.push(row);
        }
        return lines;
    }

    // CSV Export
    btnExport.addEventListener('click', () => {
        if (history.length === 0) {
            alert('保存された履歴がありません。');
            return;
        }
        
        try {
            const csvContent = arrayToCSV(history);
            const blob = new Blob([new Uint8Array([0xEF, 0xBB, 0xBF]), csvContent], { type: 'text/csv;charset=utf-8;' });
            
            const date = new Date();
            const yyyy = date.getFullYear();
            const mm = String(date.getMonth() + 1).padStart(2, '0');
            const dd = String(date.getDate()).padStart(2, '0');
            
            const link = document.createElement("a");
            const url = URL.createObjectURL(blob);
            link.setAttribute("href", url);
            link.setAttribute("download", `anima_prompts_${yyyy}${mm}${dd}.csv`);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            showTemporaryNotice('CSVをエクスポートしました。');
        } catch (e) {
            console.error(e);
            alert('CSVのエクスポート中にエラーが発生しました: ' + e.message);
        }
    });

    // CSV Import trigger
    btnImport.addEventListener('click', () => {
        csvImportFile.click();
    });

    // CSV Import action
    csvImportFile.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;
        
        const reader = new FileReader();
        reader.onload = function(evt) {
            try {
                const text = evt.target.result;
                const rows = parseCSV(text);
                
                if (rows.length < 2) {
                    throw new Error('有効なCSVデータが見つかりません。ヘッダーと少なくとも1行のデータが必要です。');
                }
                
                const header = rows[0].map(h => h.trim().toLowerCase());
                
                const colIdx = {
                    name: header.indexOf('name'),
                    datetime: header.indexOf('datetime'),
                    jaPrompt: header.indexOf('japrompt'),
                    extraTags: header.indexOf('extratags'),
                    english: header.indexOf('english'),
                    anima: header.indexOf('anima'),
                    negative: header.indexOf('negative'),
                    temp: header.indexOf('temperature'),
                    maxTokens: header.indexOf('maxtokens'),
                    fuzzy: header.indexOf('fuzzycutoff'),
                    translate: header.indexOf('translatefirst')
                };
                
                if (colIdx.jaPrompt === -1 || colIdx.anima === -1) {
                    throw new Error('CSVに必要な列 (JaPrompt, Anima) が見つかりません。');
                }
                
                let importCount = 0;
                
                for (let i = 1; i < rows.length; i++) {
                    const row = rows[i];
                    if (row.length === 1 && row[0] === '') continue;
                    
                    const jaPromptVal = row[colIdx.jaPrompt] || '';
                    if (!jaPromptVal) continue;
                    
                    const item = {
                        id: (Date.now() + i).toString(),
                        name: colIdx.name !== -1 ? (row[colIdx.name] || 'インポートした項目') : 'インポートした項目',
                        datetime: colIdx.datetime !== -1 ? (row[colIdx.datetime] || new Date().toLocaleString()) : new Date().toLocaleString(),
                        jaPrompt: jaPromptVal,
                        extraTags: colIdx.extraTags !== -1 ? (row[colIdx.extraTags] || '') : '',
                        english: colIdx.english !== -1 ? (row[colIdx.english] || '') : '',
                        anima: colIdx.anima !== -1 ? (row[colIdx.anima] || '') : '',
                        negative: colIdx.negative !== -1 ? (row[colIdx.negative] || '') : '',
                        issues: [],
                        settings: {
                            temperature: colIdx.temp !== -1 && row[colIdx.temp] !== '' ? parseFloat(row[colIdx.temp]) : 0.4,
                            maxTokens: colIdx.maxTokens !== -1 && row[colIdx.maxTokens] !== '' ? parseInt(row[colIdx.maxTokens], 10) : 2048,
                            fuzzyCutoff: colIdx.fuzzy !== -1 && row[colIdx.fuzzy] !== '' ? parseInt(row[colIdx.fuzzy], 10) : 90,
                            translateFirst: colIdx.translate !== -1 ? (row[colIdx.translate].toLowerCase() === 'true') : true
                        }
                    };
                    
                    history.push(item);
                    importCount++;
                }
                
                if (importCount > 0) {
                    localStorage.setItem('anima_prompt_history', JSON.stringify(history));
                    renderHistory();
                    showTemporaryNotice(`${importCount}件の履歴をインポートしました。`);
                } else {
                    alert('インポート可能なデータがありませんでした。');
                }
                
            } catch (err) {
                console.error(err);
                alert('インポート中にエラーが発生しました: ' + err.message);
            } finally {
                csvImportFile.value = '';
            }
        };
        
        reader.readAsText(file, 'UTF-8');
    });

    // Show temporary toast notice
    function showTemporaryNotice(message) {
        const existing = document.querySelector('.temporary-notice');
        if (existing) existing.remove();
        
        const notice = document.createElement('div');
        notice.className = 'temporary-notice';
        notice.style.position = 'fixed';
        notice.style.bottom = '20px';
        notice.style.right = '20px';
        notice.style.background = 'rgba(139, 92, 246, 0.9)';
        notice.style.color = '#fff';
        notice.style.padding = '0.75rem 1.5rem';
        notice.style.borderRadius = 'var(--border-radius-md)';
        notice.style.boxShadow = '0 10px 15px -3px rgba(0, 0, 0, 0.3)';
        notice.style.zIndex = '9999';
        notice.style.fontSize = '0.9rem';
        notice.style.fontWeight = '500';
        notice.style.backdropFilter = 'blur(8px)';
        notice.style.border = '1px solid rgba(255, 255, 255, 0.2)';
        notice.style.pointerEvents = 'none';
        notice.style.animation = 'slide-in 0.2s ease-out';
        notice.textContent = message;
        
        document.body.appendChild(notice);
        
        setTimeout(() => {
            notice.style.transition = 'opacity 0.5s ease';
            notice.style.opacity = '0';
            setTimeout(() => notice.remove(), 500);
        }, 3000);
    }

    // Initialize history
    loadHistoryFromStorage();
});
