
document.addEventListener('DOMContentLoaded', function () {
    // Select elements
    const fileInput = document.getElementById('fileInput');
    const fileLabel = document.querySelector('.file-input-label');
    const convertBtn = document.getElementById('convertBtn');
    const uploadForm = document.getElementById('uploadForm');
    const processingMessage = document.getElementById('processingMessage');
    const customPromptSection = document.getElementById('customPromptSection');
    const customPromptTextarea = document.getElementById('customPrompt');
    const charCount = document.getElementById('charCount');
    const uploadCard = document.querySelector('.upload-card');

    // --- File Input Logic ---
    if (fileInput) {
        fileInput.addEventListener('change', function () {
            if (this.files.length > 0) {
                const file = this.files[0];
                const fileName = file.name;
                const fileSize = (file.size / 1024 / 1024).toFixed(2);

                // Update file label
                fileLabel.innerHTML = `
                    <i class="fas fa-file-pdf"></i>
                    <span class="file-text">${fileName}</span>
                    <span class="file-size">${fileSize} MB</span>
                `;
                fileLabel.classList.add('file-selected');
                fileLabel.style.borderColor = '#10b981';
                fileLabel.style.background = '#f0fdf4';

                removeError();
            } else {
                resetFileLabel();
            }
        });
    }

    function resetFileLabel() {
        if (fileLabel) {
            fileLabel.innerHTML = `
                <i class="fas fa-file-pdf"></i>
                <span class="file-text">Choose PDF File</span>
                <span class="file-size">Max 16MB</span>
            `;
            fileLabel.classList.remove('file-selected');
            fileLabel.style.borderColor = '#4f46e5';
            fileLabel.style.background = '#f8faff';
        }
    }

    // --- Drag and Drop ---
    if (fileLabel) {
        fileLabel.addEventListener('dragover', function (e) {
            e.preventDefault();
            this.style.borderColor = '#4f46e5';
            this.style.background = '#f0f4ff';
        });

        fileLabel.addEventListener('dragleave', function (e) {
            e.preventDefault();
            if (!this.contains(e.relatedTarget)) {
                this.style.borderColor = '#4f46e5';
                this.style.background = '#f8faff';
            }
        });

        fileLabel.addEventListener('drop', function (e) {
            e.preventDefault();
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                fileInput.files = files;
                fileInput.dispatchEvent(new Event('change'));
            }
        });
    }

    // --- Custom Prompt Logic ---
    if (customPromptTextarea) {
        charCount.textContent = customPromptTextarea.value.length;

        customPromptTextarea.addEventListener('input', function () {
            const count = this.value.length;
            charCount.textContent = count;
            if (count < 20) charCount.style.color = '#dc2626';
            else if (count < 50) charCount.style.color = '#f59e0b';
            else charCount.style.color = '#10b981';
        });

        // Initialize display based on radio buttons
        const updateCustomUI = () => {
            const selected = document.querySelector('input[name="conversion_type"]:checked');
            if (selected && selected.value === 'custom') {
                customPromptSection.style.display = 'block';
                convertBtn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> Convert with Custom Rules';
                uploadCard.classList.add('custom-mode');
            } else {
                customPromptSection.style.display = 'none';
                convertBtn.innerHTML = '<i class="fas fa-magic"></i> Convert to Excel';
                uploadCard.classList.remove('custom-mode');
            }
        };

        document.querySelectorAll('input[name="conversion_type"]').forEach(radio => {
            radio.addEventListener('change', updateCustomUI);
        });
    }

    // Example buttons
    document.querySelectorAll('.example-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            customPromptTextarea.value = this.dataset.prompt;
            customPromptTextarea.dispatchEvent(new Event('input'));
            customPromptTextarea.focus();
        });
    });

    // --- Form Submission & Polling ---
    if (uploadForm) {
        uploadForm.addEventListener('submit', function (e) {
            e.preventDefault();

            const file = fileInput.files[0];
            const selectedConversion = document.querySelector('input[name="conversion_type"]:checked');

            // Validation
            if (!file) {
                showError('Please select a PDF file');
                return;
            }
            if (!selectedConversion) {
                showError('Please select a conversion type');
                return;
            }
            if (selectedConversion.value === 'custom' && !customPromptTextarea.value.trim()) {
                showError('Please enter custom instructions');
                customPromptTextarea.focus();
                return;
            }

            // Show Processing UI
            processingMessage.style.display = 'flex';
            convertBtn.disabled = true;
            convertBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing...';

            // Submit
            const formData = new FormData(this);
            fetch(this.action, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(res => {
                    if (res.redirected) {
                        window.location.href = res.url;
                        return;
                    }
                    const contentType = res.headers.get("content-type");
                    if (contentType && contentType.indexOf("application/json") !== -1) {
                        return res.json();
                    } else {
                        // Handle non-JSON response (e.g. 502 Bad Gateway HTML)
                        throw new Error(`Server returned ${res.status}: ${res.statusText}`);
                    }
                })
                .then(data => {
                    if (!data) return;
                    if (data.task_id) {
                        startPolling(data.task_id);
                    } else if (data.error) {
                        showError(data.error);
                        resetUI();
                    }
                })
                .catch(err => {
                    console.error('Upload error:', err);
                    showError('Upload failed. Please try again.');
                    resetUI();
                });
        });
    }

    function startPolling(taskId) {
        const progressBar = document.getElementById('mainProgressBar');
        const progressText = document.getElementById('mainProgressText');
        const subStatusText = document.getElementById('mainSubStatusText');

        const pollInterval = setInterval(() => {
            fetch(`${window.APP_ROOT || ''}/progress/${taskId}`)
                .then(res => {
                    const contentType = res.headers.get("content-type");
                    if (contentType && contentType.indexOf("application/json") !== -1) {
                        return res.json();
                    } else {
                        throw new Error(`Server returned ${res.status}`);
                    }
                })
                .then(data => {
                    if (!data) return;
                    const progress = Math.round(data.progress || 0);
                    if (progressBar) progressBar.style.width = `${progress}%`;
                    if (progressText) progressText.textContent = `${progress}%`;
                    if (subStatusText && data.message) subStatusText.textContent = data.message;

                    if (data.status === 'completed') {
                        clearInterval(pollInterval);
                        if (subStatusText) subStatusText.textContent = 'Success! Redirecting...';
                        setTimeout(() => window.location.href = `${window.APP_ROOT || ''}/result/${taskId}`, 800);
                    } else if (data.status === 'failed') {
                        clearInterval(pollInterval);
                        showError('Conversion failed: ' + (data.message || 'Unknown error'));
                        resetUI();
                    }
                })
                .catch(err => console.error('Polling error:', err));
        }, 1000);
    }

    function resetUI() {
        if (processingMessage) processingMessage.style.display = 'none';
        if (convertBtn) {
            convertBtn.disabled = false;
            const selected = document.querySelector('input[name="conversion_type"]:checked');
            convertBtn.innerHTML = (selected && selected.value === 'custom')
                ? '<i class="fas fa-wand-magic-sparkles"></i> Convert with Custom Rules'
                : '<i class="fas fa-magic"></i> Convert to Excel';
        }
    }

    // --- Internal Helpers ---
    function showError(message) {
        removeError();
        const errorDiv = document.createElement('div');
        errorDiv.className = 'error-message';
        errorDiv.innerHTML = `
            <i class="fas fa-exclamation-circle"></i>
            <div class="error-details">
                <strong>Action Required</strong>
                <span>${message}</span>
            </div>
        `;
        // Insert after file label for visibility
        fileLabel.insertAdjacentElement('afterend', errorDiv);
        setTimeout(removeError, 5000);
    }

    function removeError() {
        const existing = document.querySelector('.error-message');
        if (existing) existing.remove();
    }

    // --- Robot Soccer Physics Engine ---
    function initRobotSoccer() {
        const pitch = document.querySelector('.soccer-pitch');
        const robot = document.getElementById('robotPlayer');
        const ball = document.getElementById('soccerBall');
        const processingMsg = document.getElementById('processingMessage');
        if (!pitch || !robot || !ball || !processingMsg) return;

        let pitchW = pitch.clientWidth || 600;
        let pitchH = pitch.clientHeight || 200;

        // Physics State
        let ballX = pitchW / 2;
        let ballY = pitchH / 2;
        let ballVX = 0;
        let ballVY = 0;
        let robotX = 50;
        let robotY = 50;

        const friction = 0.985;
        const bounce = 0.7;
        const robotSpeed = 3.2;

        function update() {
            // Keep running physics even if display is none (or stop, up to you)
            // But if hidden, we shouldn't waste too much CPU
            if (getComputedStyle(processingMsg).display === 'none') {
                requestAnimationFrame(update);
                return;
            }

            pitchW = pitch.clientWidth;
            pitchH = pitch.clientHeight;

            // 1. Ball Physics
            ballVX *= friction;
            ballVY *= friction;

            ballX += ballVX;
            ballY += ballVY;

            // Wall Bounces
            if (ballX <= 10) { ballX = 10; ballVX *= -bounce; }
            if (ballX >= pitchW - 30) { ballX = pitchW - 30; ballVX *= -bounce; }
            if (ballY <= 10) { ballY = 10; ballVY *= -bounce; }
            if (ballY >= pitchH - 30) { ballY = pitchH - 30; ballVY *= -bounce; }

            // 2. Robot AI (Chase Ball like human)
            const dx = ballX - robotX;
            const dy = ballY - robotY;
            const dist = Math.sqrt(dx * dx + dy * dy);

            if (dist > 35) {
                robotX += (dx / dist) * robotSpeed;
                robotY += (dy / dist) * robotSpeed;

                // Rotation towards ball
                const angle = Math.atan2(dy, dx) * (180 / Math.PI);
                robot.style.transform = `rotate(${angle}deg)`;
            } else {
                // KICK!
                const force = 12 + Math.random() * 8;
                const angle = (Math.random() - 0.5) * 2 * Math.PI;
                ballVX = Math.cos(angle) * force;
                ballVY = Math.sin(angle) * force;

                // Animation feedback
                const currentAngle = Math.atan2(dy, dx) * (180 / Math.PI);
                robot.style.transition = 'transform 0.1s ease-out';
                robot.style.transform = `rotate(${currentAngle}deg) scale(1.15)`;

                setTimeout(() => {
                    robot.style.transition = 'none';
                    robot.style.transform = `rotate(${currentAngle}deg) scale(1)`;
                }, 150);
            }

            // Update DOM
            ball.style.left = ballX + 'px';
            ball.style.top = ballY + 'px';
            robot.style.left = robotX + 'px';
            robot.style.top = robotY + 'px';

            requestAnimationFrame(update);
        }

        requestAnimationFrame(update);
    }

    // Call init when dashboard is ready
    initRobotSoccer();
});