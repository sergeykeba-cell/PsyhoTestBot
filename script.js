let current = 0;
let answers = [];
let testType = '';
let name = Telegram.WebApp.initDataUnsafe.user?.first_name + ' ' + (Telegram.WebApp.initDataUnsafe.user?.last_name || '') || 'Анонім';

const tests = {
  pcl5: {
    title: "PCL-5 (ПТСР)",
    questions: [
      "Повторювані, турбуючі спогади, думки або образи стресової події?",
      "Повторювані, турбуючі сни про стресову подію?",
      "Раптові відчуття або дії, ніби стресова подія відбувається знову?",
      "Сильний емоційний біль при нагадуванні про подію?",
      "Фізичні реакції (серцебиття, пітливість) при нагадуванні?",
      "Уникнення думок, почуттів або розмов, пов’язаних з подією?",
      "Уникнення людей, місць або ситуацій, що нагадують подію?",
      "Нездатність згадати важливі частини події?",
      "Сильно негативні переконання про себе, інших або світ?",
      "Постійне звинувачення себе або інших?",
      "Негативні емоційні стани (страх, жах, гнів, провина)?",
      "Втрата інтересу до важливих занять?",
      "Відчуття відстороненості або відчуженості?",
      "Нездатність відчувати позитивні емоції?",
      "Дратівлива поведінка, спалахи гніву?",
      "Надмірна пильність?",
      "Надмірна настороженість?",
      "Легко лякатися?",
      "Проблеми з концентрацією?",
      "Проблеми зі сном?"
    ],
    options: ["Зовсім ні", "Трохи", "Помірно", "Відчутно", "Дуже"],
    values: [0, 1, 2, 3, 4],
    getSeverity: (s) => s <= 32 ? "мінімальний" : s <= 44 ? "легкий" : s <= 56 ? "помірний" : "важкий"
  },
  minmult: {
    title: "Міні-Мульт (скорочений MMPI)",
    questions: [
      "У Вас добрий апетит.",
      "Вранці Ви зазвичай відчуваєте, що виспалися і відпочили.",
      "У Вашому повсякденному житті маса цікавого.",
      "Ви працюєте з великою напругою.",
      // ... додайте всі 71 питання (скорочено для прикладу)
      "Останнє питання Міні-Мульт."
    ],
    options: ["Так", "Ні"],
    values: [1, 0],
    getSeverity: (s) => "результат обчислений (T-бали не підраховані в цій версії)"
  },
  schmishek: {
    title: "Шмішек (акцентуації характеру)",
    questions: [
      "Ви зазвичай спокійні, веселі?",
      "Чи легко Ви ображаєтеся, засмучуєтеся?",
      "Чи легко Ви можете розплакатися?",
      // ... додайте всі 88 питань
      "Останнє питання Шмішека."
    ],
    options: ["Так", "Ні"],
    values: [1, 0],
    getSeverity: (s) => "результат обчислений (акцентуації не підраховані в цій версії)"
  }
};

function startTest(type) {
  testType = type;
  current = 0;
  answers = new Array(tests[type].questions.length).fill(null);

  document.getElementById('start').style.display = 'none';
  document.getElementById('test').style.display = 'block';
  document.getElementById('title').textContent = tests[type].title;
  showQuestion();
}

function showQuestion() {
  const q = tests[testType].questions[current];
  document.getElementById('question').textContent = q;
  document.getElementById('current').textContent = current + 1;
  document.getElementById('total').textContent = tests[testType].questions.length;
  document.getElementById('next').disabled = true;

  const opts = document.getElementById('options');
  opts.innerHTML = '';
  tests[testType].options.forEach((text, i) => {
    const btn = document.createElement('button');
    btn.className = 'option-btn';
    btn.textContent = text;
    btn.onclick = () => select(i);
    opts.appendChild(btn);
  });
}

function select(i) {
  answers[current] = tests[testType].values[i];
  document.querySelectorAll('.option-btn').forEach(b => b.classList.remove('selected'));
  document.querySelectorAll('.option-btn')[i].classList.add('selected');
  document.getElementById('next').disabled = false;
}

function nextQuestion() {
  if (answers[current] === null) return;
  current++;
  if (current >= tests[testType].questions.length) {
    showResult();
  } else {
    showQuestion();
  }
}

function showResult() {
  document.getElementById('test').style.display = 'none';
  document.getElementById('result').style.display = 'block';

  const score = answers.reduce((a,b)=>a+b,0);
  const sev = tests[testType].getSeverity(score);

  document.getElementById('resultText').innerHTML = `
    Пацієнт: ${name}<br>
    Тест: ${tests[testType].title}<br>
    Бали: ${score}<br>
    Рівень: ${sev}
  `;
}

function downloadPDF() {
  const { jsPDF } = window.jspdf;
  const doc = new jsPDF();

  doc.setFont("DejaVuSans", "normal");
  doc.setFontSize(16);
  doc.text(`Тест: ${tests[testType].title}`, 20, 20);
  doc.setFontSize(12);
  doc.text(`Пацієнт: ${name}`, 20, 35);
  doc.text(`Дата: ${new Date().toLocaleDateString('uk-UA')}`, 20, 45);
  doc.text(`Бали: ${answers.reduce((a,b)=>a+b,0)}`, 20, 55);
  doc.text(`Рівень: ${tests[testType].getSeverity(answers.reduce((a,b)=>a+b,0))}`, 20, 65);
  doc.save(`result_${name.replace(/\s+/g, '_')}.pdf`);
}

function sendToDoctor() {
  const score = answers.reduce((a,b)=>a+b,0);
  const sev = tests[testType].getSeverity(score);
  Telegram.WebApp.sendData(JSON.stringify({
    name: name,
    test: tests[testType].title,
    score: score,
    severity: sev,
    answers: answers
  }));
  Telegram.WebApp.close();
}

function restart() {
  location.reload();
}

Telegram.WebApp.ready();
Telegram.WebApp.expand();
