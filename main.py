"""
Passerelle Optimmo – outil d'aide au diagnostiqueur DPE.
Lance une icône en barre des tâches permettant d'envoyer
le dossier LICIEL actif à Optimmo pour analyse avant validation.
"""
import single_instance
import tray

if __name__ == "__main__":
    if single_instance.acquire():
        tray.run()
    else:
        single_instance.notify_already_running()
