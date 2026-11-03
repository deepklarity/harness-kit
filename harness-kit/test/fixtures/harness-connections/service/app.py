from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/orders/<int:oid>")
def get_order(oid):
    return jsonify(id=oid)
