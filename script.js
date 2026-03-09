let currentQuestion = 0;
let answers = [];
let testType = '';
let fullName = Telegram.WebApp.initDataUnsafe.user?.first_name || 'Анонім';
let questions = [];
let options = [];

const tests = {
  pcl5: {
    title: "PCL-5 (ПТСР)",
    questions: [
      "Повторювані, турбуючі спогади, думки або образи стресової події?",
      "Повторювані, турбуючі сни про стресову подію?",
      "Раптові відчуття або дії, ніби стресова подія відбувається знову?",
      // ... додайте всі 20 питань PCL-5
      "Проблеми зі сном?"
    ],
    options: ["0 - Зовсім ні", "1 - Трохи", "2 - Помірно", "3 - Дуже сильно", "4 - Надзвичайно"],
    values: [0,1,2,3,4],
    severity: (score) => score <= 32 ? "мінімальний" : score <= 44 ? "легкий" : score <= 56 ? "помірний" : "важкий"
  },
  // Додайте інші тести аналогічно
};

function startTest(type) {
  testType = type;
  questions = tests[type].questions;
  options = tests[type].options;
  answers = new Array(questions.length).fill(null);

  document.getElementById('start-screen').style.display = 'none';
  document.getElementById('test-screen').style.display = 'block';
  document.getElementById('test-title').textContent = tests[type].title;
  showQuestion(0);
}

function showQuestion(index) {
  currentQuestion = index;
  document.getElementById('question').textContent = questions[index];
  document.getElementById('current').textContent = index + 1;
  document.getElementById('total').textContent = questions.length;
  document.getElementById('next-btn').disabled = true;

  const optionsDiv = document.getElementById('options');
  optionsDiv.innerHTML = '';

  options.forEach((opt, i) => {
    const btn = document.createElement('button');
    btn.textContent = opt;
    btn.onclick = () => selectAnswer(i);
    optionsDiv.appendChild(btn);
  });
}

function selectAnswer(valueIndex) {
  answers[currentQuestion] = tests[testType].values[valueIndex];
  document.getElementById('next-btn').disabled = false;
}

function nextQuestion() {
  if (answers[currentQuestion] === null) return;

  if (currentQuestion + 1 >= questions.length) {
    showResult();
  } else {
    showQuestion(currentQuestion + 1);
  }
}

function showResult() {
  document.getElementById('test-screen').style.display = 'none';
  document.getElementById('result-screen').style.display = 'block';

  const score = answers.reduce((a, b) => a + b, 0);
  const severity = tests[testType].severity(score);

  document.getElementById('result-text').innerHTML = `
    <p>Пацієнт: ${fullName}</p>
    <p>Тест: ${tests[testType].title}</p>
    <p>Бали: ${score}</p>
    <p>Рівень: ${severity}</p>
  `;
}

function generatePDF() {
  const score = answers.reduce((a, b) => a + b, 0);
  const severity = tests[testType].severity(score);

  const { jsPDF } = window.jspdf;
  const doc = new jsPDF();

  doc.setFont("DejaVuSans", "normal");
  doc.setFontSize(16);
  doc.text(`Тест: ${tests[testType].title}`, 20, 20);
  doc.setFontSize(12);
  doc.text(`Пацієнт: ${fullName}`, 20, 30);
  doc.text(`Дата: ${new Date().toLocaleDateString('uk-UA')}`, 20, 40);
  doc.text(`Бали: ${score} | Рівень: ${severity}`, 20, 50);

  doc.save(`result_${fullName.replace(/\s+/g, '_')}.pdf`);
}

function sendToDoctor() {
  Telegram.WebApp.sendData(JSON.stringify({
    full_name: fullName,
    test_type: testType,
    score: answers.reduce((a, b) => a + b, 0),
    severity: tests[testType].severity(score),
    answers: answers
  }));
  Telegram.WebApp.close();
}

function restart() {
  location.reload();
}

// Ініціалізація
Telegram.WebApp.ready();
Telegram.WebApp.expand();
