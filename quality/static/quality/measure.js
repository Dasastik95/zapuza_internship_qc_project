// Turns the value box GREEN or RED as the inspector types.
// This is only visual feedback. The server checks the limits again when you save.
document.querySelectorAll(".measure-form").forEach(function (form) {
  var lower = Number(form.dataset.lower);
  var upper = Number(form.dataset.upper);
  var input = form.querySelector(".value-input");
  var message = form.querySelector(".out-msg");

  input.addEventListener("input", function () {
    input.classList.remove("is-valid", "is-invalid");
    message.classList.add("d-none");

    if (input.value === "") {
      return;
    }
    var value = Number(input.value);
    if (value >= lower && value <= upper) {
      input.classList.add("is-valid");
    } else {
      input.classList.add("is-invalid");
      message.classList.remove("d-none");
    }
  });
});
