document.addEventListener('DOMContentLoaded', () => {
    // Initialize Lucide Icons
    lucide.createIcons();

    // Element references
    const btnGenerate = document.getElementById('btn-generate');
    const inputPrompt = document.getElementById('input-prompt');
    const inputTags = document.getElementById('input-tags');
    const suggestChips = document.querySelectorAll('.suggest-chip');
    
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
});
