from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/users/<int:uid>")
def get_user(uid):
    return jsonify(id=uid)


@app.get("/reports/weekly")
def weekly_report():
    return jsonify(rows=[])
