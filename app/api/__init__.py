from app.api import auth, tasks, nodes, cluster, programmes_admin


def register_blueprints(app):
    app.register_blueprint(auth.bp)
    app.register_blueprint(tasks.bp)
    app.register_blueprint(nodes.bp)
    app.register_blueprint(cluster.bp)
    app.register_blueprint(programmes_admin.bp)
