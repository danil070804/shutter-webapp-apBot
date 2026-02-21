// Инициализация Telegram WebApp
let tg = null;
let isTelegram = false;

try {
    if (window.Telegram && window.Telegram.WebApp) {
        tg = window.Telegram.WebApp;
        isTelegram = true;
        tg.expand();
        tg.ready();
        tg.setHeaderColor('#0a0a0a');
        tg.setBackgroundColor('#0a0a0a');
    }
} catch (e) {
    console.log('Telegram WebApp not found');
}

// Получаем данные из URL параметров (переданные ботом)
const urlParams = new URLSearchParams(window.location.search);
const userData = {
    id: urlParams.get('uid') || tg?.initDataUnsafe?.user?.id || '0',
    username: urlParams.get('uname') || tg?.initDataUnsafe?.user?.username || 'guest',
    profits_count: parseInt(urlParams.get('profits')) || 0,
    profits_sum: parseInt(urlParams.get('sum')) || 0,
    current_streak: parseInt(urlParams.get('streak')) || 0,
    max_streak: parseInt(urlParams.get('max_streak')) || 0,
    goal: parseInt(urlParams.get('goal')) || 0,
    role: urlParams.get('role') || 'worker',
    mentor_id: urlParams.get('mentor') || ''
};

// Расчет ранга
function getRank(profits) {
    if (profits >= 100) return {name: 'LEGEND', emoji: '👑', color: '#ffd700'};
    if (profits >= 50) return {name: 'MASTER', emoji: '💎', color: '#00ffff'};
    if (profits >= 25) return {name: 'ELITE', emoji: '🏆', color: '#ff6b00'};
    if (profits >= 10) return {name: 'SENIOR', emoji: '🥈', color: '#c0c0c0'};
    if (profits >= 3) return {name: 'WORKER', emoji: '🔵', color: '#4169e1'};
    return {name: 'NEW', emoji: '🟢', color: '#00ff00'};
}

const rank = getRank(userData.profits_count);

// Форматирование чисел
function formatMoney(num) {
    return num.toLocaleString('ru-RU');
}

// Инициализация приложения
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        document.getElementById('loader').style.opacity = '0';
        setTimeout(() => {
            document.getElementById('loader').style.display = 'none';
            document.getElementById('mainContent').style.display = 'block';
            loadRealData();
            initCharts();
            updateStreakTimer();
        }, 500);
    }, 1500);
});

function loadRealData() {
    // Заполняем реальными данными из бота
    document.getElementById('username').textContent = '@' + (userData.username || 'user');
    document.getElementById('userId').textContent = 'ID: ' + userData.id;
    document.getElementById('rankFlair').textContent = rank.name;
    document.getElementById('roleBadge').textContent = userData.role.toUpperCase();

    // Основная статистика
    animateValue('totalEarned', 0, userData.profits_sum, 1500, true);
    animateValue('totalDeals', 0, userData.profits_count, 1000);

    // Прогресс к следующему рангу
    const nextThreshold = userData.profits_count < 3 ? 3 :
                         userData.profits_count < 10 ? 10 :
                         userData.profits_count < 25 ? 25 :
                         userData.profits_count < 50 ? 50 : 100;
    const prevThreshold = userData.profits_count < 3 ? 0 :
                         userData.profits_count < 10 ? 3 :
                         userData.profits_count < 25 ? 10 :
                         userData.profits_count < 50 ? 25 : 50;
    const progress = ((userData.profits_count - prevThreshold) / (nextThreshold - prevThreshold)) * 100;

    document.getElementById('progressPercent').textContent = Math.round(progress) + '%';
    document.getElementById('rankProgress').style.width = progress + '%';
    document.getElementById('currentProgress').textContent = userData.profits_count + ' / ' + nextThreshold;
    document.getElementById('nextRankName').textContent = 'NEXT RANK';

    // Streak
    if (userData.current_streak > 0) {
        document.getElementById('streakBanner').style.display = 'flex';
        document.getElementById('streakCount').textContent = userData.current_streak + ' DAYS';
    } else {
        document.getElementById('streakBanner').style.display = 'none';
    }

    // Доп. инфо
    document.getElementById('daysInTeam').textContent = '— дней'; // Можно добавить в передачу данных
    document.getElementById('globalRank').textContent = '#—';

    // Цель
    if (userData.goal > 0) {
        const goalProgress = Math.min((userData.profits_count / userData.goal) * 100, 100);
        document.querySelector('.progress-elite').innerHTML += `
            <div style="margin-top:10px; padding-top:10px; border-top:1px solid var(--border);">
                <div class="progress-header">
                    <span>🎯 Цель: ${userData.goal} профитов</span>
                    <span>${Math.round(goalProgress)}%</span>
                </div>
                <div class="progress-bar-bg">
                    <div class="progress-bar-fill" style="width: ${goalProgress}%; background: linear-gradient(90deg, #00c853, #64dd17);"></div>
                </div>
            </div>
        `;
    }

    // Реферальная ссылка
    const refLink = `https://t.me/${tg?.initDataUnsafe?.user?.username || 'bot'}?start=ref${userData.id}`;
    document.getElementById('refLink').textContent = refLink;

    // Активность (демо-график на основе реальных данных)
    generateActivityChart();
}

function generateActivityChart() {
    // Генерируем график на основе реальных профитов (случайное распределение по дням)
    const baseValue = Math.max(1, Math.floor(userData.profits_count / 7));
    const data = [];
    for (let i = 0; i < 7; i++) {
        data.push(Math.floor(baseValue * (0.5 + Math.random())));
    }

    const ctx = document.getElementById('activityChart');
    if (ctx && typeof Chart !== 'undefined') {
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'],
                datasets: [{
                    label: 'Профиты',
                    data: data,
                    borderColor: '#ff6b00',
                    backgroundColor: 'rgba(255,107,0,0.1)',
                    borderWidth: 3,
                    tension: 0.4,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { color: '#888' }
                    },
                    x: {
                        grid: { display: false },
                        ticks: { color: '#888' }
                    }
                }
            }
        });
    }
}

function animateValue(id, start, end, duration, isCurrency = false) {
    const obj = document.getElementById(id);
    if (!obj) return;

    const range = end - start;
    const minTimer = 50;
    const stepTime = Math.abs(Math.floor(duration / range)) || 50;
    let startTime = new Date().getTime();
    let endTime = startTime + duration;
    let timer;

    function run() {
        let now = new Date().getTime();
        let remaining = Math.max((endTime - now) / duration, 0);
        let value = Math.round(end - (remaining * range));

        obj.textContent = isCurrency ? value.toLocaleString() : value;

        if (value == end) {
            clearInterval(timer);
        }
    }

    timer = setInterval(run, stepTime);
    run();
}

function initCharts() {
    // Дополнительные графики если нужны
    const incomeCtx = document.getElementById('incomeChart');
    if (incomeCtx && typeof Chart !== 'undefined') {
        new Chart(incomeCtx, {
            type: 'bar',
            data: {
                labels: ['Нед 1', 'Нед 2', 'Нед 3', 'Нед 4'],
                datasets: [{
                    label: 'Доход',
                    data: [
                        userData.profits_sum * 0.2,
                        userData.profits_sum * 0.3,
                        userData.profits_sum * 0.25,
                        userData.profits_sum * 0.25
                    ],
                    backgroundColor: '#ff6b00',
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } }
            }
        });
    }
}

function updateStreakTimer() {
    const timerEl = document.getElementById('streakTimer');
    if (!timerEl) return;

    setInterval(() => {
        const now = new Date();
        const midnight = new Date();
        midnight.setHours(24, 0, 0, 0);
        const diff = midnight - now;

        const h = Math.floor(diff / 3600000);
        const m = Math.floor((diff % 3600000) / 60000);
        const s = Math.floor((diff % 60000) / 1000);

        timerEl.textContent = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }, 1000);
}

function switchTab(tab) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));

    const targetTab = document.getElementById(tab + '-tab');
    if (targetTab) targetTab.classList.add('active');

    const targetBtn = document.querySelector(`[data-tab="${tab}"]`);
    if (targetBtn) targetBtn.classList.add('active');

    if (isTelegram && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('light');
    }
}

function copyRef() {
    const link = document.getElementById('refLink').textContent;
    navigator.clipboard.writeText(link).then(() => {
        showToast('Успешно', 'Ссылка скопирована');
    });
    if (isTelegram && tg.HapticFeedback) {
        tg.HapticFeedback.notificationOccurred('success');
    }
}

function showToast(title, message) {
    const toast = document.getElementById('toast');
    if (!toast) return;

    toast.querySelector('.toast-title').textContent = title;
    toast.querySelector('.toast-message').textContent = message;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}

function refreshData() {
    if (isTelegram && tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('medium');
    }
    showToast('Обновление', 'Загрузка данных...');
    // Можно добавить перезагрузку с новыми параметрами
}

function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
}