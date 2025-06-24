// Telegram Web App initialization
const tg = window.Telegram?.WebApp;
if (tg) {
    tg.expand();
    tg.setViewportHeight(window.innerHeight);
}
const userId = (tg?.initDataUnsafe?.user?.id || new URLSearchParams(window.location.search).get('user_id'))?.toString();
const API_URL = 'https://zebi-bingo-bot.vercel.app';
const playerInfo = document.getElementById('playerInfo');
let gameId = null;
let selectedNumber = null;
let currentBet = null;

// Validate userId
if (!userId) {
    console.error('No user ID found');
    showErrorPage('User ID is missing');
}

// DOM Elements
const welcomePage = document.getElementById('welcomePage');
const registerPage = document.getElementById('registerPage');
const returnToBotBtn = document.getElementById('returnToBot');
const checkBalanceBtn = document.getElementById('checkBalance');
const withdrawMoneyBtn = document.getElementById('withdrawMoney');
const topLeadersBtn = document.getElementById('topLeaders');
const inviteFriendsBtn = document.getElementById('inviteFriends');
const adminMenuBtn = document.getElementById('adminMenu');
const gameArea = document.getElementById('gameArea');
const bingoCard = document.getElementById('bingoCard');
const gameStatus = document.getElementById('gameStatus');
const callBtn = document.getElementById('callBtn');
const bingoBtn = document.getElementById('bingoBtn');
const contentDiv = document.getElementById('content');
const calledNumbersDiv = document.getElementById('calledNumbers');
const nightModeSwitch = document.getElementById('nightModeSwitch');
const devInfo = document.getElementById('devInfo');
const loadingPage = document.getElementById('loadingPage');
const errorPage = document.getElementById('errorPage');
const startBtn = document.getElementById('startBtn');
const registerBtn = document.getElementById('registerBtn');

function showPage(page) {
    document.querySelectorAll('.content').forEach(p => p.style.display = 'none');
    page.style.display = 'flex';
}

function showErrorPage(message) {
    document.getElementById('errorMessage').textContent = message;
    showPage(errorPage);
}

// Set custom background with fallback
function setCustomBackground() {
    const customBg = new Image();
    customBg.src = '/background.jpg'; // Custom background image
    customBg.onload = () => {
        document.body.style.backgroundImage = `url(${customBg.src})`;
        document.body.style.backgroundSize = 'cover';
        document.body.style.backgroundPosition = 'center';
    };
    customBg.onerror = () => {
        console.warn('Custom background failed to load, using fallback');
        document.body.style.backgroundImage = 'none';
    };
}

// Registration handler
async function registerUser() {
    const phone = document.getElementById('phoneInput').value;
    const username = document.getElementById('usernameInput').value;
    const referralCode = document.getElementById('referralInput').value;
    const errorDiv = document.getElementById('registerError');

    if (!phone || !username) {
        errorDiv.textContent = 'ስልክ ቁጥር እና የተጠቃሚ ስም ያስፈልጋሉ!';
        return;
    }

    try {
        const response = await fetch(`${API_URL}/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, phone, username, referral_code: referralCode })
        });
        const data = await response.json();
        if (data.status === 'success') {
            showPage(document.getElementById('mainPage'));
            updatePlayerInfo();
            checkAdminStatus();
        } else {
            errorDiv.textContent = data.reason || 'ምዝገባ አልተሳካም!';
        }
    } catch (error) {
        errorDiv.textContent = `አንድነት ችግር: ${error.message}`;
    }
}

startBtn.addEventListener('click', () => showPage(registerPage));
if (registerBtn) {
    registerBtn.addEventListener('click', registerUser);
}

async function checkRegistration() {
    showPage(welcomePage);
    await new Promise(resolve => setTimeout(resolve, 2000));
    loadingPage.style.display = 'flex';
    try {
        const response = await fetch(`${API_URL}/user_data?user_id=${userId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (data.error || !data.registered) {
            showPage(registerPage);
        } else {
            showPage(document.getElementById('mainPage'));
            updatePlayerInfo();
            checkAdminStatus();
        }
    } catch (error) {
        console.error('Error checking registration:', error);
        showErrorPage('Failed to check registration. Please try again.');
    } finally {
        loadingPage.style.display = 'none';
    }
}

async function checkAdminStatus() {
    try {
        const response = await fetch(`${API_URL}/user_data?user_id=${userId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (data.error) throw new Error(data.error);
        adminMenuBtn.style.display = data.role === 'admin' ? 'block' : 'none';
        document.getElementById('createGameBtn').style.display = data.role === 'admin' ? 'block' : 'none';
    } catch (error) {
        console.error('Error checking admin status:', error);
        adminMenuBtn.style.display = 'none';
    }
}

async function updatePlayerInfo() {
    try {
        const response = await fetch(`${API_URL}/user_data?user_id=${userId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (data.error) throw new Error(data.error);
        const username = data.username || `User_${userId}`;
        playerInfo.textContent = `👤 ${username} | 💰 ${data.wallet} ETB`;
        if (data.referral_bonus > 0) {
            alert(`🎉 You earned ${data.referral_bonus} ETB from referrals!`);
        }
    } catch (error) {
        console.error('Error updating player info:', error);
        playerInfo.textContent = `👤 User_${userId} | 💰 Error`;
    }
}

function generateBingoCard(cardNumbers) {
    if (!cardNumbers || cardNumbers.length !== 25) return;
    bingoCard.innerHTML = '';
    const letters = ['B', 'I', 'N', 'G', 'O'];
    for (let i = 0; i < 5; i++) {
        const letter = document.createElement('div');
        letter.className = 'letter';
        letter.textContent = letters[i];
        bingoCard.appendChild(letter);
    }
    for (let i = 0; i < 25; i++) {
        const cell = document.createElement('div');
        cell.className = 'cell';
        cell.textContent = cardNumbers[i];
        if (i === 12) cell.innerHTML = '<span class="star">★</span>';
        cell.onclick = () => cell.classList.toggle('marked');
        bingoCard.appendChild(cell);
    }
    bingoCard.style.gridTemplateColumns = 'repeat(5, 1fr)';
}

function updateCard(calledNumbers) {
    if (!calledNumbers) return;
    const cells = bingoCard.getElementsByClassName('cell');
    for (let cell of cells) {
        cell.classList.remove('marked');
        if (cell.textContent && calledNumbers.includes(parseInt(cell.textContent))) {
            cell.classList.add('marked');
        }
    }
}

function updateGameStatus() {
    if (!gameId) return;
    fetch(`${API_URL}/game_status?game_id=${gameId}&user_id=${userId}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'not_found') {
                gameStatus.textContent = 'Game not found';
                return;
            }
            gameStatus.textContent = `Status: ${data.status} | ${data.start_time ? new Date(data.start_time).toLocaleString() : 'Not Started'} - ${data.end_time ? new Date(data.end_time).toLocaleString() : 'Not Ended'} | Prize: ${data.prize_amount} ETB | Called: ${data.numbers_called.length} | Winner: ${data.winner_id || 'None'} | Players: ${data.players.length}`;
            updateCard(data.numbers_called);
            calledNumbersDiv.textContent = `Called Numbers: ${data.numbers_called.join(', ') || 'None'}`;
            if (data.card_numbers && data.card_numbers.length) {
                generateBingoCard(data.card_numbers);
            }
            if (data.selected_numbers && data.selected_numbers.length) {
                const inactiveNumbers = document.getElementById('inactiveNumbers') || document.createElement('div');
                inactiveNumbers.id = 'inactiveNumbers';
                inactiveNumbers.innerHTML = data.selected_numbers.map(n => `<span class="inactive">${n}</span>`).join(', ');
                gameArea.appendChild(inactiveNumbers);
            }
            if (data.status === 'finished' && data.winner_id) {
                showPostWinOptions(data.bet_amount);
            }
        })
        .catch(error => {
            console.error('Error updating game status:', error);
            gameStatus.textContent = 'Error fetching game status';
        });
}

returnToBotBtn.addEventListener('click', () => {
    if (tg) tg.close();
});

checkBalanceBtn.addEventListener('click', async () => {
    contentDiv.style.display = 'block';
    gameArea.style.display = 'none';
    try {
        const response = await fetch(`${API_URL}/user_data?user_id=${userId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (data.error) throw new Error(data.error);
        contentDiv.innerHTML = `
            <h2>💰 የዋሌት ገጽ</h2>
            <p>ዋሌት: ${data.wallet} ETB</p>
            <p>ያሸነፉት ጨዋታ: ${data.wins} ETB</p>
            <p>የተሳሳተ ቢንጎ: ${data.invalid_bingo_count}</p>
        `;
        updatePlayerInfo();
    } catch (error) {
        contentDiv.innerHTML = `<p>አንድነት ችግር: ${error.message}</p>`;
    }
});

withdrawMoneyBtn.addEventListener('click', async () => {
    contentDiv.style.display = 'block';
    gameArea.style.display = 'none';
    try {
        const response = await fetch(`${API_URL}/user_data?user_id=${userId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (data.error) throw new Error(data.error);
        contentDiv.innerHTML = `
            <h2>💸 ገንዘብ ለማውጣት</h2>
            <input id="withdrawAmount" type="number" placeholder="መጠን (ETB)" min="100" />
            <select id="withdrawMethod">
                <option value="telebirr">Telebirr</option>
                <option value="cbe">CBE</option>
            </select>
            <button onclick="requestWithdrawal()">📤 ጠይቅ</button>
            <p id="withdrawMessage"></p>
        `;
        updatePlayerInfo();
    } catch (error) {
        contentDiv.innerHTML = `<p>አንድነት ችግር: ${error.message}</p>`;
    }
});

function requestWithdrawal() {
    const amountInput = document.getElementById('withdrawAmount');
    const method = document.getElementById('withdrawMethod').value;
    const amount = parseInt(amountInput.value);
    const messageDiv = document.getElementById('withdrawMessage');

    if (!amount || amount < 100) {
        messageDiv.textContent = '❌ መጠን ቢያንስ 100 ETB መሆን አለበት!';
        return;
    }
    fetch(`${API_URL}/request_withdrawal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, amount, method })
    })
        .then(response => response.json())
        .then(data => {
            messageDiv.textContent = data.status === 'requested'
                ? `✅ ጥያቄዎ ተልኳል (ID: ${data.withdraw_id})`
                : `❌ ${data.reason}`;
            updatePlayerInfo();
        })
        .catch(error => {
            messageDiv.textContent = `አንድነት ችግር: ${error.message}`;
        });
}

topLeadersBtn.addEventListener('click', async () => {
    contentDiv.style.display = 'block';
    gameArea.style.display = 'none';
    try {
        const response = await fetch(`${API_URL}/leaderboard`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        let tableHtml = `
            <h2>🏆 የመሪዎች ዝርዝር</h2>
            <table class="leaderboard-table">
                <tr><th>ቦታ</th><th>ስም</th><th>ነጥብ</th><th>ዋሌት (ETB)</th></tr>
        `;
        data.leaders.forEach((user, index) => {
            tableHtml += `
                <tr><td>${index + 1}</td><td>${user.username}</td><td>${user.score}</td><td>${user.wallet}</td></tr>
            `;
        });
        tableHtml += '</table>';
        contentDiv.innerHTML = tableHtml;
        updatePlayerInfo();
    } catch (error) {
        contentDiv.innerHTML = `<p>አንድነት ችግር: ${error.message}</p>`;
    }
});

inviteFriendsBtn.addEventListener('click', async () => {
    contentDiv.style.display = 'block';
    gameArea.style.display = 'none';
    try {
        const response = await fetch(`${API_URL}/invite_data?user_id=${userId}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (data.error) throw new Error(data.error);
        contentDiv.innerHTML = `
            <h2>👥 ጓደኞችን ጋብዝ</h2>
            <p>ሪፈራል ሊንክ: <a href="${data.referral_link}" target="_blank">${data.referral_link}</a></p>
            <p>የጋበዙት ጓደኞች: ${data.referral_count}</p>
            <p>20 ጓደኞችን በመጋበዝ 10 ETB ያግኙ!</p>
        `;
        updatePlayerInfo();
    } catch (error) {
        contentDiv.innerHTML = `<p>አንድነት ችግር: ${error.message}</p>`;
    }
});

adminMenuBtn.addEventListener('click', async () => {
    try {
        const response = await fetch(`${API_URL}/user_data?user_id=${userId}`);
        const data = await response.json();
        if (data.error || data.role !== 'admin') {
            contentDiv.style.display = 'block';
            gameArea.style.display = 'none';
            contentDiv.innerHTML = '<p>አስተዳዳሪነት አልተፈቀደም!</p>';
            return;
        }
        const withdrawalsResponse = await fetch(`${API_URL}/pending_withdrawals?user_id=${userId}`);
        const withdrawalsData = await withdrawalsResponse.json();
        contentDiv.style.display = 'block';
        gameArea.style.display = 'none';
        contentDiv.innerHTML = `
            <h2>🛠 አስተዳዳሪ ገጽ</h2>
            <div class="admin-form">
                <h3>አዲስ አስተዳዳሪ ለመጨመር</h3>
                <input id="newAdminId" placeholder="የተጠቃሚ ID" />
                <button onclick="promoteToAdmin()">👑 አስተዳዳሪ አድርግ</button>
                <h3>ጨዋታ ለመፍጠር</h3>
                <select id="betAmount">
                    <option value="10">10 ETB</option>
                    <option value="50">50 ETB</option>
                    <option value="100">100 ETB</option>
                    <option value="200">200 ETB</option>
                </select>
                <button onclick="createGame()">🎮 ጨዋታ ፍጠር</button>
                <h3>የፋይናንስ ማረጋገጫ</h3>
                <input id="txId" placeholder="የፋይናንስ መረጃ ID" />
                <button onclick="adminAction('verify_payment')">✅ የፋይናንስ ማረጋገጫ</button>
                <h3>Pending Withdrawals</h3>
                ${withdrawalsData.withdrawals.map(w => `
                    <div>
                        ID: ${w.withdraw_id} | User: ${w.user_id} | Amount: ${w.amount} ETB | Method: ${w.method} | Time: ${new Date(w.request_time).toLocaleString()}
                        <input id="note_${w.withdraw_id}" placeholder="Note" />
                        <button onclick="manageWithdrawal('${w.withdraw_id}', 'approve')">✅ Approve</button>
                        <button onclick="manageWithdrawal('${w.withdraw_id}', 'reject')">❌ Reject</button>
                    </div>
                `).join('')}
            </div>
        `;
        updatePlayerInfo();
    } catch (error) {
        contentDiv.innerHTML = `<p>አንድነት ችግር: ${error.message}</p>`;
    }
});

async function promoteToAdmin() {
    const newAdminId = document.getElementById('newAdminId').value;
    if (!newAdminId) {
        alert('እባክዎ የተጠቃሚ ID ያስገቡ');
        return;
    }
    try {
        const response = await fetch(`${API_URL}/add_admin`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, target_user_id: newAdminId })
        });
        const data = await response.json();
        alert(data.status === 'success'
            ? `User ${newAdminId} promoted to admin successfully!`
            : `Error: ${data.reason || 'Failed to promote user'}`);
    } catch (error) {
        alert(`አንድነት ችግር: ${error.message}`);
    }
}

async function createGame() {
    const betAmount = parseInt(document.getElementById('betAmount').value);
    try {
        const response = await fetch(`${API_URL}/create_game`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, bet_amount: betAmount })
        });
        const data = await response.json();
        if (data.status === 'failed') throw new Error(data.reason);
        gameId = data.game_id;
        currentBet = data.bet_amount;
        contentDiv.style.display = 'none';
        gameArea.style.display = 'block';
        gameStatus.textContent = `Status: ${data.status} | Bet: ${data.bet_amount} ETB`;
        alert(`Game ${gameId} created with ${betAmount} ETB bet!`);
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

document.getElementById('joinBtn').addEventListener('click', async () => {
    contentDiv.style.display = 'block';
    gameArea.style.display = 'none';
    contentDiv.innerHTML = `
        <h2>👥 ጨዋታ ይቀላቀሉ</h2>
        <button onclick="joinGame(10)">10 ETB</button>
        <button onclick="joinGame(50)">50 ETB</button>
        <button onclick="joinGame(100)">100 ETB</button>
        <button onclick="joinGame(200)">200 ETB</button>
    `;
});

async function joinGame(betAmount) {
    try {
        const response = await fetch(`${API_URL}/join_game`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, bet_amount: betAmount })
        });
        const data = await response.json();
        if (data.status === 'failed') throw new Error(data.reason);
        gameId = data.game_id;
        currentBet = data.bet_amount;
        contentDiv.style.display = 'none';
        gameArea.style.display = 'block';
        gameStatus.textContent = `Status: ${data.status} | Bet: ${data.bet_amount} ETB`;
        displayNumberSelector();
    } catch (error) {
        contentDiv.innerHTML = `<p>አንድነት ችግር: ${error.message}</p>`;
    }
}

function displayNumberSelector() {
    let html = '<div id="numberSelector" class="number-grid">';
    for (let i = 1; i <= 100; i++) {
        html += `<button class="number-btn" onclick="selectCardNumber(${i})">${i}</button>`;
    }
    html += '</div>';
    gameArea.innerHTML = html + gameArea.innerHTML;
}

function selectCardNumber(selectedNum) {
    selectedNumber = selectedNum;
    fetch(`${API_URL}/select_number`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, game_id: gameId, selected_number: selectedNum })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'failed') {
                alert(data.reason);
                document.querySelector(`.number-btn[onclick="selectCardNumber(${selectedNum})"]`).disabled = true;
            } else {
                document.getElementById('numberSelector').style.display = 'none';
                const previewCard = document.createElement('div');
                previewCard.id = 'previewCard';
                for (let i = 0; i < 25; i++) {
                    const cell = document.createElement('div');
                    cell.className = 'cell';
                    cell.textContent = data.card_numbers[i];
                    if (i === 12) cell.innerHTML = '<span class="star">★</span>';
                    previewCard.appendChild(cell);
                }
                previewCard.style.gridTemplateColumns = 'repeat(5, 1fr)';
                gameArea.insertBefore(previewCard, bingoCard);
                const acceptBtn = document.createElement('button');
                acceptBtn.textContent = 'Accept';
                acceptBtn.onclick = acceptCard;
                const cancelBtn = document.createElement('button');
                cancelBtn.textContent = 'Cancel';
                cancelBtn.onclick = cancelCard;
                gameArea.appendChild(acceptBtn);
                gameArea.appendChild(cancelBtn);
            }
        });
}

function acceptCard() {
    fetch(`${API_URL}/accept_card`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, game_id: gameId })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'accepted') {
                document.getElementById('previewCard').remove();
                document.querySelectorAll('#gameArea button').forEach(btn => btn.remove());
                generateBingoCard(data.card_numbers);
                updateGameStatus();
                setInterval(updateGameStatus, 5000);
            }
        });
}

function cancelCard() {
    document.getElementById('previewCard').remove();
    document.querySelectorAll('#gameArea button').forEach(btn => btn.remove());
    displayNumberSelector();
}

function showPostWinOptions(betAmount) {
    gameArea.innerHTML = `
        <div id="postWinMessage">${gameStatus.textContent}</div>
        <button onclick="continuePlay(${betAmount})">Continue Play</button>
        <button onclick="backToBetSelection()">Back to Bet Selection</button>
    `;
    gameId = null;
}

function continuePlay(betAmount) {
    gameId = null;
    currentBet = betAmount;
    contentDiv.style.display = 'none';
    gameArea.style.display = 'block';
    gameStatus.textContent = `Status: Waiting | Bet: ${betAmount} ETB`;
    displayNumberSelector();
}

function backToBetSelection() {
    gameId = null;
    currentBet = null;
    contentDiv.style.display = 'block';
    gameArea.style.display = 'none';
    contentDiv.innerHTML = `
        <h2>👥 ጨዋታ ይቀላቀሉ</h2>
        <button onclick="joinGame(10)">10 ETB</button>
        <button onclick="joinGame(50)">50 ETB</button>
        <button onclick="joinGame(100)">100 ETB</button>
        <button onclick="joinGame(200)">200 ETB</button>
    `;
}

callBtn.addEventListener('click', async () => {
    if (!gameId) return;
    try {
        const response = await fetch(`${API_URL}/call_number`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, game_id: gameId })
        });
        const data = await response.json();
        gameStatus.textContent = `Called: ${data.number} | Remaining: ${data.remaining}`;
        updateCard(data.called_numbers);
        updatePlayerInfo();
    } catch (error) {
        gameStatus.textContent = `Error: ${error.message}`;
    }
});

bingoBtn.addEventListener('click', async () => {
    if (!gameId) return;
    try {
        const response = await fetch(`${API_URL}/check_bingo`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, game_id: gameId })
        });
        const data = await response.json();
        gameStatus.textContent = data.message;
        if (data.kicked) {
            alert('❌ Invalid Bingo! You have been removed from this game.');
            backToBetSelection();
        } else if (data.won) {
            alert(data.message);
            updateGameStatus();
        }
        updatePlayerInfo();
    } catch (error) {
        gameStatus.textContent = `Error: ${error.message}`;
    }
});

async function adminAction(action) {
    const txId = document.getElementById('txId')?.value;
    let payload = { user_id: userId, action };
    if (action === 'verify_payment') payload.tx_id = txId;

    try {
        const response = await fetch(`${API_URL}/admin_actions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        contentDiv.innerHTML = `<p>${data.status === 'verified' ? `✅ ${data.amount} ETB ለ${data.user_id} ተጠበቃ!` : `✅ ${data.status}!`}</p>`;
        updatePlayerInfo();
    } catch (error) {
        contentDiv.innerHTML = `<p>አንድነት ችግር: ${error.message}</p>`;
    }
}

function manageWithdrawal(withdrawId, actionType) {
    const adminNote = document.getElementById(`note_${withdrawId}`).value;
    fetch(`${API_URL}/admin_actions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, action: 'manage_withdrawal', withdraw_id: withdrawId, action_type: actionType, admin_note: adminNote })
    })
        .then(response => response.json())
        .then(data => {
            contentDiv.innerHTML = `<p>${data.status === 'approved' ? `✅ ${data.amount} ETB withdrawn for User ${data.user_id}` : `❌ ${data.status}`}</p>`;
            adminMenuBtn.click();
            updatePlayerInfo();
        })
        .catch(error => {
            contentDiv.innerHTML = `<p>አንድነት ችግር: ${error.message}</p>`;
        });
}

// Night Mode Toggle
nightModeSwitch.addEventListener('change', () => {
    document.body.classList.toggle('night-mode', nightModeSwitch.checked);
    document.getElementById('app').classList.toggle('night-mode', nightModeSwitch.checked);
    setCustomBackground(); // Re-apply background in case mode changes
});

// Interactive Developer Info
let isHovering = false;
let isClicked = false;

devInfo.addEventListener('mouseover', () => {
    if (!isClicked) {
        devInfo.textContent = '0913252238';
        isHovering = true;
    }
});

devInfo.addEventListener('mouseout', () => {
    if (!isClicked && isHovering) {
        devInfo.textContent = 'Developed by Benzion Creatives 2025';
        isHovering = false;
    }
});

devInfo.addEventListener('click', () => {
    if (!isClicked) {
        devInfo.textContent = '0913252238';
        isClicked = true;
    } else {
        devInfo.textContent = 'Developed by Benzion Creatives 2025';
        isClicked = false;
    }
});

// Initialize
setCustomBackground();
checkRegistration();
updatePlayerInfo();